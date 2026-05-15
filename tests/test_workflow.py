"""Tests for the review-loop state machine."""

from pathlib import Path

import pytest

from agent_review_loop.models import ApprovalPolicy, MessageType, Role, SessionStatus, Turn
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


def test_consensus_policy_should_require_editor_to_accept_reviewer_approval(
    tmp_path: Path,
) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(
        document,
        approval_policy=ApprovalPolicy.CONSENSUS,
    )
    workflow = ReviewWorkflow(store)

    workflow.send_message(
        session.id,
        role=Role.EDITOR,
        message_type=MessageType.REVIEW_REQUEST,
        body="Review this from a failure-mode angle.",
    )
    reviewer_approval = workflow.send_message(
        session.id,
        role=Role.REVIEWER,
        message_type=MessageType.APPROVED,
        body="Approved for this review prompt.",
    )

    pending_session = store.get_session(session.id)
    assert reviewer_approval["state"] == "approval_pending"
    assert pending_session.status == SessionStatus.ACTIVE
    assert pending_session.turn == Turn.EDITOR

    next_action = workflow.next_action(session.id, role=Role.EDITOR)
    assert next_action["state"] == "ready"
    assert next_action["action"] == "accept_approval_or_request_another_review"

    accepted = workflow.send_message(
        session.id,
        role=Role.EDITOR,
        message_type=MessageType.APPROVED,
        body="APPROVED after enough perspectives.",
    )

    assert accepted["state"] == "terminal"
    assert store.get_session(session.id).status == SessionStatus.APPROVED
    assert store.get_session(session.id).turn == Turn.NONE


def test_consensus_policy_should_allow_editor_to_request_another_review_after_approval(
    tmp_path: Path,
) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(
        document,
        max_rounds=3,
        approval_policy=ApprovalPolicy.CONSENSUS,
    )
    workflow = ReviewWorkflow(store)

    workflow.send_message(
        session.id,
        role=Role.EDITOR,
        message_type=MessageType.REVIEW_REQUEST,
        body="Review this from a fresh-eyes angle.",
    )
    workflow.send_message(
        session.id,
        role=Role.REVIEWER,
        message_type=MessageType.APPROVED,
        body="Approved for fresh-eyes review.",
    )
    another_review = workflow.send_message(
        session.id,
        role=Role.EDITOR,
        message_type=MessageType.REVIEW_REQUEST,
        body="Now review this from a failure-mode angle.",
    )

    assert another_review["state"] == "accepted"
    assert another_review["session"]["turn"] == Turn.REVIEWER.value
    assert another_review["session"]["current_round"] == 2


def test_editor_should_not_approve_before_reviewer_approval_in_consensus_policy(
    tmp_path: Path,
) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(
        document,
        approval_policy=ApprovalPolicy.CONSENSUS,
    )
    workflow = ReviewWorkflow(store)

    with pytest.raises(WorkflowError, match="after reviewer approval"):
        workflow.send_message(
            session.id,
            role=Role.EDITOR,
            message_type=MessageType.APPROVED,
            body="APPROVED",
        )


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
