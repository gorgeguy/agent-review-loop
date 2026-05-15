"""Tests for the review-loop state machine."""

from pathlib import Path

import pytest

from agent_review_loop.models import MessageType, Role, SessionStatus, Turn
from agent_review_loop.store import Store
from agent_review_loop.workflow import ReviewWorkflow, WorkflowError


def test_should_transition_through_review_and_approval(tmp_path: Path) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(document, max_rounds=2)
    workflow = ReviewWorkflow(store)

    first = workflow.send_message(
        session.id,
        role=Role.EDITOR,
        message_type=MessageType.REVIEW_REQUEST,
        body="Review my changes.",
    )
    assert first["session"]["turn"] == Turn.REVIEWER.value

    feedback = workflow.send_message(
        session.id,
        role=Role.REVIEWER,
        message_type=MessageType.REVIEW_FEEDBACK,
        body="Tighten the opening.",
    )
    assert feedback["session"]["turn"] == Turn.EDITOR.value

    document.write_text("# Draft\n\nTighter opening.\n", encoding="utf-8")
    workflow.send_message(
        session.id,
        role=Role.EDITOR,
        message_type=MessageType.REVISION_REPORT,
        body="Changed opening. Rejected nothing.",
    )
    approved = workflow.send_message(
        session.id,
        role=Role.REVIEWER,
        message_type=MessageType.APPROVED,
        body="Approved.",
    )

    assert approved["state"] == "terminal"
    assert store.get_session(session.id).status == SessionStatus.APPROVED
    assert store.get_session(session.id).turn == Turn.NONE


def test_should_reject_out_of_turn_message_without_mutating_state(tmp_path: Path) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(document)
    workflow = ReviewWorkflow(store)

    with pytest.raises(WorkflowError, match="editor's turn"):
        workflow.send_message(
            session.id,
            role=Role.REVIEWER,
            message_type=MessageType.REVIEW_FEEDBACK,
            body="Feedback too early.",
        )

    assert store.get_session(session.id).turn == Turn.EDITOR
    assert store.list_messages(session.id) == []


def test_should_terminate_after_max_round_feedback(tmp_path: Path) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(document, max_rounds=1)
    workflow = ReviewWorkflow(store)

    workflow.send_message(
        session.id,
        role=Role.EDITOR,
        message_type=MessageType.REVIEW_REQUEST,
        body="Review my changes.",
    )
    result = workflow.send_message(
        session.id,
        role=Role.REVIEWER,
        message_type=MessageType.REVIEW_FEEDBACK,
        body="Still needs work.",
    )

    final_session = store.get_session(session.id)
    assert result["state"] == "terminal"
    assert final_session.status == SessionStatus.MAX_ROUNDS_REACHED
    assert final_session.turn == Turn.NONE
    assert store.get_decision(session.id) is not None


def test_next_action_should_include_prior_other_role_message(tmp_path: Path) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(document, max_rounds=2)
    workflow = ReviewWorkflow(store)

    workflow.send_message(
        session.id,
        role=Role.EDITOR,
        message_type=MessageType.REVIEW_REQUEST,
        body="Review my changes.",
    )
    workflow.send_message(
        session.id,
        role=Role.REVIEWER,
        message_type=MessageType.REVIEW_FEEDBACK,
        body="Add examples.",
    )

    action = workflow.next_action(session.id, role=Role.EDITOR, context=1)

    assert action["state"] == "ready"
    assert action["messages"][0]["body"] == "Add examples."
