"""Typed domain models for Agent Review Loop."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """Participant roles recorded in the conversation."""

    EDITOR = "editor"
    REVIEWER = "reviewer"
    SYSTEM = "system"


class MessageType(StrEnum):
    """Supported protocol message types."""

    REVIEW_REQUEST = "review_request"
    REVIEW_FEEDBACK = "review_feedback"
    REVISION_REPORT = "revision_report"
    APPROVED = "approved"
    MAX_ROUNDS_REACHED = "max_rounds_reached"


class SessionStatus(StrEnum):
    """Lifecycle status for a review session."""

    ACTIVE = "active"
    APPROVED = "approved"
    MAX_ROUNDS_REACHED = "max_rounds_reached"


class Turn(StrEnum):
    """Whose turn it is to act next."""

    EDITOR = "editor"
    REVIEWER = "reviewer"
    NONE = "none"


TERMINAL_STATUSES = {SessionStatus.APPROVED, SessionStatus.MAX_ROUNDS_REACHED}


@dataclass(frozen=True)
class Session:
    """A durable review session."""

    id: str
    document_path: str
    max_rounds: int
    current_round: int
    status: SessionStatus
    turn: Turn
    socket_path: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DocumentVersion:
    """A content-addressed snapshot of the reviewed document."""

    id: int
    session_id: str
    round: int
    file_hash: str
    snapshot_path: str
    created_at: str


@dataclass(frozen=True)
class Message:
    """A message exchanged through the broker."""

    id: int
    session_id: str
    role: Role
    type: MessageType
    round: int
    body: str
    created_at: str


@dataclass(frozen=True)
class Decision:
    """Terminal decision for a review session."""

    id: int
    session_id: str
    status: SessionStatus
    reason: str
    created_at: str
