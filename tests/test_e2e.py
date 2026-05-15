"""End-to-end workflow tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep

from typer.testing import CliRunner

from agent_review_loop.broker import serve_session
from agent_review_loop.cli import app
from agent_review_loop.store import Store

runner = CliRunner()


def test_cli_workflow_should_revise_and_approve_document(tmp_path: Path) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(document, max_rounds=3)
    stop, thread = start_server(session.id, tmp_path)

    try:
        run_cli(
            tmp_path,
            [
                "send",
                "--role",
                "editor",
                "--session",
                session.id,
                "--type",
                "review_request",
                "--body",
                "Review my changes.",
            ],
        )
        run_cli(
            tmp_path,
            [
                "send",
                "--role",
                "reviewer",
                "--session",
                session.id,
                "--type",
                "review_feedback",
                "--body",
                "Add a concrete example.",
            ],
        )
        document.write_text("# Draft\n\nExample added.\n", encoding="utf-8")
        run_cli(
            tmp_path,
            [
                "send",
                "--role",
                "editor",
                "--session",
                session.id,
                "--type",
                "revision_report",
                "--body",
                "Added a concrete example. Rejected nothing.",
            ],
        )
        approved = run_cli(
            tmp_path,
            [
                "send",
                "--role",
                "reviewer",
                "--session",
                session.id,
                "--type",
                "approved",
                "--body",
                "APPROVED",
            ],
        )
    finally:
        stop.set()
        thread.join(timeout=2)

    payload = json.loads(approved.output)["payload"]
    monitor = json.loads(run_cli(tmp_path, ["monitor", "--session", session.id]).output)["payload"]
    assert payload["decision"]["status"] == "approved"
    assert monitor["session"]["status"] == "approved"
    assert len(monitor["messages"]) == 4
    assert (
        Path(monitor["latest_version"]["snapshot_path"])
        .read_text(encoding="utf-8")
        .endswith("Example added.\n")
    )


def test_next_wait_should_resume_when_role_turn_arrives(tmp_path: Path) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(document)
    stop, thread = start_server(session.id, tmp_path)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent_review_loop.cli",
            "next",
            "--role",
            "reviewer",
            "--session",
            session.id,
            "--wait",
            "--poll-interval",
            "0.01",
        ],
        cwd=Path.cwd(),
        env=env_for(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        sleep(0.1)
        assert process.poll() is None
        run_cli(
            tmp_path,
            [
                "send",
                "--role",
                "editor",
                "--session",
                session.id,
                "--type",
                "review_request",
                "--body",
                "Review my changes.",
            ],
        )
        stdout, stderr = process.communicate(timeout=2)
    finally:
        if process.poll() is None:
            process.terminate()
        stop.set()
        thread.join(timeout=2)

    assert stderr == ""
    payload = json.loads(stdout)["payload"]
    assert payload["state"] == "ready"
    assert payload["action"] == "review_document"


def run_cli(tmp_path: Path, args: list[str]):
    result = runner.invoke(app, args, env=env_for(tmp_path))
    assert result.exit_code == 0, result.output
    return result


def start_server(session_id: str, tmp_path: Path) -> tuple[Event, Thread]:
    store = Store(tmp_path / ".arl")
    session = store.get_session(session_id)
    stop = Event()
    thread = Thread(
        target=serve_session,
        kwargs={
            "session_id": session.id,
            "home": tmp_path / ".arl",
            "stop_event": stop,
            "install_signal_handlers": False,
        },
        daemon=True,
    )
    thread.start()
    wait_for_socket(Path(session.socket_path))
    return stop, thread


def env_for(tmp_path: Path) -> dict[str, str]:
    return {**os.environ, "ARL_HOME": str(tmp_path / ".arl")}


def wait_for_socket(path: Path) -> None:
    deadline = monotonic() + 2
    while monotonic() < deadline:
        if path.exists():
            return
        sleep(0.01)
    raise AssertionError(f"socket did not appear: {path}")
