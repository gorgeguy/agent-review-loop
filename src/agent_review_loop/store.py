"""SQLite persistence and document snapshot storage."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from agent_review_loop.models import (
    Decision,
    DocumentVersion,
    Message,
    MessageType,
    Role,
    Session,
    SessionStatus,
    Turn,
)
from agent_review_loop.paths import database_path, snapshots_dir, socket_path

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}


class StoreError(ValueError):
    """Raised when persisted state cannot satisfy a request."""


class Store:
    """Repository for sessions, messages, decisions, and document snapshots."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = database_path(home)
        self._ensure_schema()

    def create_session(self, document_path: Path, *, max_rounds: int = 5) -> Session:
        """Create a review session for an existing Markdown or text document."""

        resolved = document_path.expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise StoreError(f"Document does not exist: {resolved}")
        if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise StoreError("Only Markdown and text documents are supported in v1")
        if max_rounds < 1:
            raise StoreError("max_rounds must be at least 1")

        session_id = f"arl-{uuid.uuid4().hex[:8]}"
        now = utc_now()
        sock_path = socket_path(self.home, session_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, document_path, max_rounds, current_round, status, turn,
                    socket_path, created_at, updated_at
                )
                VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(resolved),
                    max_rounds,
                    SessionStatus.ACTIVE.value,
                    Turn.EDITOR.value,
                    str(sock_path),
                    now,
                    now,
                ),
            )
        session = self.get_session(session_id)
        self.snapshot_document(session, round_number=0)
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> Session:
        """Load a session by id."""

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise StoreError(f"Unknown session: {session_id}")
        return row_to_session(row)

    def set_turn(
        self,
        session_id: str,
        *,
        turn: Turn,
        current_round: int | None = None,
    ) -> Session:
        """Persist the next turn and optionally the current round."""

        now = utc_now()
        with self._connect() as conn:
            if current_round is None:
                conn.execute(
                    "UPDATE sessions SET turn = ?, updated_at = ? WHERE id = ?",
                    (turn.value, now, session_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE sessions
                    SET turn = ?, current_round = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (turn.value, current_round, now, session_id),
                )
        return self.get_session(session_id)

    def append_message(
        self,
        session_id: str,
        *,
        role: Role,
        message_type: MessageType,
        round_number: int,
        body: str,
    ) -> Message:
        """Append one conversation message."""

        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages (session_id, role, type, round, body, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, role.value, message_type.value, round_number, body, now),
            )
            message_id = cursor.lastrowid
        if message_id is None:
            raise StoreError("Could not append message")
        return self.get_message(message_id)

    def get_message(self, message_id: int) -> Message:
        """Load one message by integer id."""

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise StoreError(f"Unknown message: {message_id}")
        return row_to_message(row)

    def list_messages(self, session_id: str) -> list[Message]:
        """Return all messages for a session in creation order."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [row_to_message(row) for row in rows]

    def latest_version(self, session_id: str) -> DocumentVersion:
        """Return the newest recorded document version."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM document_versions
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise StoreError(f"No document versions for session: {session_id}")
        return row_to_document_version(row)

    def snapshot_document(self, session: Session, *, round_number: int) -> DocumentVersion:
        """Hash and snapshot the session document."""

        document = Path(session.document_path)
        digest = sha256_file(document)
        snapshot = self._snapshot_path(document, digest)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot.exists():
            shutil.copyfile(document, snapshot)

        now = utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM document_versions
                WHERE session_id = ? AND file_hash = ?
                """,
                (session.id, digest),
            ).fetchone()
            if existing is not None:
                return row_to_document_version(existing)
            cursor = conn.execute(
                """
                INSERT INTO document_versions (
                    session_id, round, file_hash, snapshot_path, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (session.id, round_number, digest, str(snapshot), now),
            )
            version_id = cursor.lastrowid
        if version_id is None:
            raise StoreError("Could not record document version")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM document_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise StoreError("Could not reload document version")
        return row_to_document_version(row)

    def record_decision(
        self,
        session_id: str,
        *,
        status: SessionStatus,
        reason: str,
    ) -> Decision:
        """Mark a session terminal and persist its decision."""

        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET status = ?, turn = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, Turn.NONE.value, now, session_id),
            )
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO decisions (id, session_id, status, reason, created_at)
                VALUES (
                    (SELECT id FROM decisions WHERE session_id = ?),
                    ?, ?, ?, ?
                )
                """,
                (session_id, session_id, status.value, reason, now),
            )
            decision_id = cursor.lastrowid
            if decision_id is None:
                row = conn.execute(
                    "SELECT * FROM decisions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM decisions WHERE id = ?", (decision_id,)
                ).fetchone()
        if row is None:
            raise StoreError("Could not record decision")
        return row_to_decision(row)

    def get_decision(self, session_id: str) -> Decision | None:
        """Return a session decision when one has been recorded."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decisions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return None if row is None else row_to_decision(row)

    def _snapshot_path(self, document_path: Path, digest: str) -> Path:
        suffix = ".md" if document_path.suffix.lower() in {".md", ".markdown"} else ".txt"
        return snapshots_dir(self.home) / f"{digest}{suffix}"

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
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

                CREATE TABLE IF NOT EXISTS document_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    round INTEGER NOT NULL,
                    file_hash TEXT NOT NULL,
                    snapshot_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, file_hash)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    type TEXT NOT NULL,
                    round INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        self.home.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""

    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hash for a file."""

    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_to_session(row: sqlite3.Row) -> Session:
    """Convert a SQLite row into a session model."""

    return Session(
        id=str(row["id"]),
        document_path=str(row["document_path"]),
        max_rounds=int(row["max_rounds"]),
        current_round=int(row["current_round"]),
        status=SessionStatus(str(row["status"])),
        turn=Turn(str(row["turn"])),
        socket_path=str(row["socket_path"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def row_to_document_version(row: sqlite3.Row) -> DocumentVersion:
    """Convert a SQLite row into a document version model."""

    return DocumentVersion(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        round=int(row["round"]),
        file_hash=str(row["file_hash"]),
        snapshot_path=str(row["snapshot_path"]),
        created_at=str(row["created_at"]),
    )


def row_to_message(row: sqlite3.Row) -> Message:
    """Convert a SQLite row into a message model."""

    return Message(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        role=Role(str(row["role"])),
        type=MessageType(str(row["type"])),
        round=int(row["round"]),
        body=str(row["body"]),
        created_at=str(row["created_at"]),
    )


def row_to_decision(row: sqlite3.Row) -> Decision:
    """Convert a SQLite row into a decision model."""

    return Decision(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        status=SessionStatus(str(row["status"])),
        reason=str(row["reason"]),
        created_at=str(row["created_at"]),
    )


def model_to_dict(model: Any) -> dict[str, Any]:
    """Convert dataclass-like models with enum values into JSON-ready dicts."""

    data: dict[str, Any] = {}
    for key, value in model.__dict__.items():
        data[key] = value.value if isinstance(value, StrEnum) else value
    return data
