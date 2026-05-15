"""Tests for durable storage and snapshot behavior."""

import sqlite3
from pathlib import Path

import pytest

from agent_review_loop.models import ApprovalPolicy, SessionStatus, Turn
from agent_review_loop.store import Store, StoreError


def test_should_create_session_with_initial_snapshot(tmp_path: Path) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")

    session = store.create_session(document, max_rounds=3)
    version = store.latest_version(session.id)

    assert session.document_path == str(document.resolve())
    assert session.max_rounds == 3
    assert session.approval_policy == ApprovalPolicy.REVIEWER_ONLY
    assert session.current_round == 0
    assert session.status == SessionStatus.ACTIVE
    assert session.turn == Turn.EDITOR
    assert Path(version.snapshot_path).read_text(encoding="utf-8") == "# Draft\n"


def test_should_create_session_with_consensus_approval_policy(tmp_path: Path) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")

    session = store.create_session(document, approval_policy=ApprovalPolicy.CONSENSUS)

    assert session.approval_policy == ApprovalPolicy.CONSENSUS


def test_should_migrate_existing_sessions_to_default_approval_policy(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".arl"
    home.mkdir()
    db_path = home / "arl.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                document_path TEXT NOT NULL,
                max_rounds INTEGER NOT NULL,
                current_round INTEGER NOT NULL,
                status TEXT NOT NULL,
                turn TEXT NOT NULL,
                socket_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    Store(home)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert "approval_policy" in columns


def test_should_reuse_snapshot_for_same_content(tmp_path: Path) -> None:
    document = tmp_path / "draft.txt"
    document.write_text("same\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(document)

    first = store.latest_version(session.id)
    second = store.snapshot_document(session, round_number=1)

    assert second.id == first.id
    assert second.snapshot_path == first.snapshot_path


def test_should_reject_unsupported_document_type(tmp_path: Path) -> None:
    document = tmp_path / "draft.pdf"
    document.write_text("not really a PDF", encoding="utf-8")
    store = Store(tmp_path / ".arl")

    with pytest.raises(StoreError, match="Markdown and text"):
        store.create_session(document)
