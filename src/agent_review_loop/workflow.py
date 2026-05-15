"""Review-loop state machine built on persistent storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_review_loop.models import (
    TERMINAL_STATUSES,
    Message,
    MessageType,
    Role,
    Session,
    SessionStatus,
    Turn,
)
from agent_review_loop.store import Store, model_to_dict


class WorkflowError(ValueError):
    """Raised when a role attempts an invalid state transition."""


class ReviewWorkflow:
    """High-level review-loop operations."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def next_action(
        self,
        session_id: str,
        *,
        role: Role,
        context: int = 5,
        full_transcript: bool = False,
    ) -> dict[str, Any]:
        """Return the pending action for a role."""

        if role == Role.SYSTEM:
            raise WorkflowError("System role cannot request next action")

        session = self.store.get_session(session_id)
        latest = self.store.latest_version(session_id)
        messages = self._context_messages(
            session_id,
            role=role,
            context=context,
            full_transcript=full_transcript,
        )
        response = {
            "session": model_to_dict(session),
            "latest_version": model_to_dict(latest),
            "messages": [model_to_dict(message) for message in messages],
        }

        if session.status in TERMINAL_STATUSES:
            return {"state": "terminal", "action": "stop", **response}
        if session.turn.value != role.value:
            return {
                "state": "waiting",
                "action": "wait",
                "waiting_for": session.turn.value,
                **response,
            }
        return {
            "state": "ready",
            "action": self._action_for(session, role),
            **response,
        }

    def send_message(
        self,
        session_id: str,
        *,
        role: Role,
        message_type: MessageType,
        body: str,
    ) -> dict[str, Any]:
        """Validate and apply one role message."""

        if not body.strip():
            raise WorkflowError("Message body cannot be empty")
        session = self.store.get_session(session_id)
        if session.status in TERMINAL_STATUSES:
            raise WorkflowError(f"Session is already terminal: {session.status.value}")
        if role == Role.SYSTEM:
            raise WorkflowError("System messages are broker-owned")
        if session.turn.value != role.value:
            raise WorkflowError(f"It is {session.turn.value}'s turn, not {role.value}'s")

        if role == Role.EDITOR:
            return self._send_editor_message(session, message_type=message_type, body=body)
        return self._send_reviewer_message(session, message_type=message_type, body=body)

    def monitor(self, session_id: str) -> dict[str, Any]:
        """Return a read-only session snapshot."""

        session = self.store.get_session(session_id)
        latest = self.store.latest_version(session_id)
        decision = self.store.get_decision(session_id)
        messages = self.store.list_messages(session_id)
        return {
            "session": model_to_dict(session),
            "latest_version": model_to_dict(latest),
            "decision": None if decision is None else model_to_dict(decision),
            "messages": [model_to_dict(message) for message in messages],
        }

    def _send_editor_message(
        self,
        session: Session,
        *,
        message_type: MessageType,
        body: str,
    ) -> dict[str, Any]:
        if message_type not in {MessageType.REVIEW_REQUEST, MessageType.REVISION_REPORT}:
            raise WorkflowError("Editor may only send review_request or revision_report")

        next_round = session.current_round + 1
        if next_round > session.max_rounds:
            return self._record_max_rounds(session, body="Maximum review rounds reached.")

        self.store.snapshot_document(session, round_number=next_round)
        message = self.store.append_message(
            session.id,
            role=Role.EDITOR,
            message_type=message_type,
            round_number=next_round,
            body=body,
        )
        next_session = self.store.set_turn(
            session.id,
            turn=Turn.REVIEWER,
            current_round=next_round,
        )
        return {
            "state": "accepted",
            "message": model_to_dict(message),
            "session": model_to_dict(next_session),
        }

    def _send_reviewer_message(
        self,
        session: Session,
        *,
        message_type: MessageType,
        body: str,
    ) -> dict[str, Any]:
        if message_type == MessageType.APPROVED:
            message = self.store.append_message(
                session.id,
                role=Role.REVIEWER,
                message_type=message_type,
                round_number=session.current_round,
                body=body,
            )
            decision = self.store.record_decision(
                session.id,
                status=SessionStatus.APPROVED,
                reason=body,
            )
            return {
                "state": "terminal",
                "message": model_to_dict(message),
                "decision": model_to_dict(decision),
                "session": model_to_dict(self.store.get_session(session.id)),
            }
        if message_type != MessageType.REVIEW_FEEDBACK:
            raise WorkflowError("Reviewer may only send review_feedback or approved")

        message = self.store.append_message(
            session.id,
            role=Role.REVIEWER,
            message_type=message_type,
            round_number=session.current_round,
            body=body,
        )
        if session.current_round >= session.max_rounds:
            return self._record_max_rounds(
                session,
                body="Maximum review rounds reached after reviewer feedback.",
                triggering_message=message,
            )
        next_session = self.store.set_turn(session.id, turn=Turn.EDITOR)
        return {
            "state": "accepted",
            "message": model_to_dict(message),
            "session": model_to_dict(next_session),
        }

    def _record_max_rounds(
        self,
        session: Session,
        *,
        body: str,
        triggering_message: Message | None = None,
    ) -> dict[str, Any]:
        message = self.store.append_message(
            session.id,
            role=Role.SYSTEM,
            message_type=MessageType.MAX_ROUNDS_REACHED,
            round_number=session.current_round,
            body=body,
        )
        decision = self.store.record_decision(
            session.id,
            status=SessionStatus.MAX_ROUNDS_REACHED,
            reason=body,
        )
        response: dict[str, Any] = {
            "state": "terminal",
            "message": model_to_dict(message),
            "decision": model_to_dict(decision),
            "session": model_to_dict(self.store.get_session(session.id)),
        }
        if triggering_message is not None:
            response["triggering_message"] = model_to_dict(triggering_message)
        return response

    def _context_messages(
        self,
        session_id: str,
        *,
        role: Role,
        context: int,
        full_transcript: bool,
    ) -> list[Message]:
        messages = self.store.list_messages(session_id)
        if full_transcript:
            return messages
        selected = messages[-max(context, 0) :] if context > 0 else []
        prior_other = next(
            (message for message in reversed(messages) if message.role not in {role, Role.SYSTEM}),
            None,
        )
        if prior_other is not None and prior_other not in selected:
            selected = [prior_other, *selected]
        return selected

    def _action_for(self, session: Session, role: Role) -> str:
        document_empty = Path(session.document_path).read_text(encoding="utf-8") == ""
        if role == Role.EDITOR and session.current_round == 0 and document_empty:
            return "draft_document_then_request_review"
        if role == Role.EDITOR and session.current_round == 0:
            return "send_review_request"
        if role == Role.EDITOR:
            return "revise_document_then_send_revision_report"
        return "review_document"


def parse_role(value: str) -> Role:
    """Parse a user-supplied role value."""

    try:
        role = Role(value)
    except ValueError as exc:
        raise WorkflowError(f"Invalid role: {value}") from exc
    if role == Role.SYSTEM:
        raise WorkflowError("System role is not user-selectable")
    return role


def parse_message_type(value: str) -> MessageType:
    """Parse a user-supplied message type value."""

    try:
        return MessageType(value)
    except ValueError as exc:
        raise WorkflowError(f"Invalid message type: {value}") from exc


def error_response(message: str) -> dict[str, Any]:
    """Return a JSON-ready error response."""

    return {"ok": False, "error": message}


def success_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready success response."""

    return {"ok": True, "payload": payload}
