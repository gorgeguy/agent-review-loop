"""Command line interface for Agent Review Loop."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, NoReturn, cast

import typer

from agent_review_loop.broker import serve_session
from agent_review_loop.client import ClientError, request_for_session
from agent_review_loop.paths import arl_home
from agent_review_loop.prompts import build_arl_command, build_role_prompt
from agent_review_loop.store import Store, StoreError
from agent_review_loop.workflow import WorkflowError, parse_role

app = typer.Typer(no_args_is_help=True)


@app.command()
def start(
    doc: Path = typer.Option(..., "--doc", exists=True, file_okay=True, dir_okay=False),
    max_rounds: int = typer.Option(5, "--max-rounds", min=1),
    startup_timeout: float = typer.Option(5.0, "--startup-timeout", hidden=True, min=0.1),
) -> None:
    """Start a new session as editor and print agent bootstrap instructions."""

    home = arl_home()
    try:
        store = Store(home)
        session = store.create_session(doc, max_rounds=max_rounds)
        broker = start_background_broker(session.id, home=home)
        wait_for_socket(Path(session.socket_path), process=broker, timeout=startup_timeout)
        typer.echo(build_start_output(store, session.id, broker.pid, broker.log_path))
    except StoreError as exc:
        fail(str(exc))


@app.command()
def join(session: str = typer.Option(..., "--session")) -> None:
    """Print the reviewer prompt for an existing session."""

    try:
        typer.echo(build_role_prompt(Store(arl_home()), session, parse_role("reviewer")))
    except (StoreError, WorkflowError) as exc:
        fail(str(exc))


@app.command()
def init(
    doc: Path = typer.Option(..., "--doc", exists=True, file_okay=True, dir_okay=False),
    max_rounds: int = typer.Option(5, "--max-rounds", min=1),
) -> None:
    """Create a review-loop session for a document."""

    try:
        session = Store(arl_home()).create_session(doc, max_rounds=max_rounds)
    except StoreError as exc:
        fail(str(exc))
    emit(
        {
            "session_id": session.id,
            "document_path": session.document_path,
            "socket_path": session.socket_path,
            "max_rounds": session.max_rounds,
        }
    )


@app.command()
def serve(session: str = typer.Option(..., "--session")) -> None:
    """Run the foreground broker for a session."""

    try:
        serve_session(session, home=arl_home())
    except StoreError as exc:
        fail(str(exc))


@app.command()
def prompt(
    role: str = typer.Option(..., "--role"),
    session: str = typer.Option(..., "--session"),
) -> None:
    """Print a role-specific prompt for an agent console."""

    try:
        parsed_role = parse_role(role)
        typer.echo(build_role_prompt(Store(arl_home()), session, parsed_role))
    except (StoreError, WorkflowError) as exc:
        fail(str(exc))


@app.command("next")
def next_command(
    role: str = typer.Option(..., "--role"),
    session: str = typer.Option(..., "--session"),
    wait: bool = typer.Option(False, "--wait"),
    context: int = typer.Option(5, "--context", min=0),
    full_transcript: bool = typer.Option(False, "--full-transcript"),
    heartbeat: float | None = typer.Option(None, "--heartbeat", min=0.01),
    poll_interval: float = typer.Option(0.5, "--poll-interval", hidden=True, min=0.01),
) -> None:
    """Return the next pending action for a role."""

    request = {
        "action": "next",
        "session_id": session,
        "role": role,
        "context": context,
        "full_transcript": full_transcript,
    }
    heartbeat_interval = heartbeat
    next_heartbeat = (
        time.monotonic() + heartbeat_interval if heartbeat_interval is not None else None
    )
    while True:
        response = broker_request(session, request)
        payload = checked_payload(response)
        if not wait or payload["state"] != "waiting":
            emit(response)
            return
        if (
            heartbeat_interval is not None
            and next_heartbeat is not None
            and time.monotonic() >= next_heartbeat
        ):
            typer.echo(
                f"Waiting for {role} work or terminal state in session {session}...",
                err=True,
            )
            next_heartbeat = time.monotonic() + heartbeat_interval
        time.sleep(poll_interval)


@app.command()
def send(
    role: str = typer.Option(..., "--role"),
    session: str = typer.Option(..., "--session"),
    message_type: str = typer.Option(..., "--type"),
    body: str | None = typer.Option(None, "--body"),
    body_file: Path | None = typer.Option(
        None, "--body-file", exists=True, file_okay=True, dir_okay=False
    ),
) -> None:
    """Send a role message to the broker."""

    resolved_body = read_body(body=body, body_file=body_file)
    response = broker_request(
        session,
        {
            "action": "send",
            "session_id": session,
            "role": role,
            "message_type": message_type,
            "body": resolved_body,
        },
    )
    if not response.get("ok"):
        fail(str(response.get("error", "Broker rejected request")))
    emit(response)


@app.command()
def monitor(
    session: str = typer.Option(..., "--session"),
    follow: bool = typer.Option(False, "--follow"),
    human: bool = typer.Option(False, "--human"),
    poll_interval: float = typer.Option(0.5, "--poll-interval", hidden=True, min=0.01),
) -> None:
    """Observe session status and messages without mutating state."""

    last_signature: tuple[str, int] | None = None
    while True:
        payload = monitor_payload(session)
        signature = (
            payload["session"]["status"],
            int(payload["messages"][-1]["id"]) if payload["messages"] else 0,
        )
        if signature != last_signature:
            if human:
                typer.echo(format_monitor_payload(payload))
            else:
                emit({"ok": True, "payload": payload})
            last_signature = signature
        if not follow or payload["session"]["status"] != "active":
            return
        time.sleep(poll_interval)


@app.command()
def status(session: str = typer.Option(..., "--session")) -> None:
    """Print compact session status."""

    payload = monitor_payload(session)
    emit(
        {
            "ok": True,
            "payload": {
                "session": payload["session"],
                "latest_version": payload["latest_version"],
                "decision": payload["decision"],
            },
        }
    )


@app.command()
def transcript(
    session: str = typer.Option(..., "--session"),
    human: bool = typer.Option(False, "--human"),
) -> None:
    """Print the conversation transcript."""

    payload = monitor_payload(session)
    if human:
        typer.echo(format_transcript(payload["messages"]))
        return
    emit({"ok": True, "payload": {"messages": payload["messages"]}})


class BackgroundBroker:
    """Started broker process details."""

    def __init__(self, pid: int, log_path: Path) -> None:
        self.pid = pid
        self.log_path = log_path


def start_background_broker(session_id: str, *, home: Path) -> BackgroundBroker:
    """Launch a broker process for a session and return process metadata."""

    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{session_id}.broker.log"
    command = [sys.executable, "-m", "agent_review_loop.cli", "serve", "--session", session_id]
    env = {**os.environ, "ARL_HOME": str(home)}
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    return BackgroundBroker(process.pid, log_path)


def wait_for_socket(path: Path, *, process: BackgroundBroker, timeout: float) -> None:
    """Wait until the broker socket appears or fail with useful diagnostics."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if not process_is_running(process.pid):
            fail(f"Broker exited before creating its socket. See log: {process.log_path}")
        time.sleep(0.05)
    fail(f"Timed out waiting for broker socket at {path}. See log: {process.log_path}")


def process_is_running(pid: int) -> bool:
    """Return whether a process still exists."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def build_start_output(store: Store, session_id: str, broker_pid: int, log_path: Path) -> str:
    """Build agent-facing bootstrap text for a new editor-owned session."""

    editor_prompt = build_role_prompt(store, session_id, parse_role("editor"))
    session = store.get_session(session_id)
    join_command = build_arl_command(store, ["join", "--session", session_id])
    monitor_command = build_arl_command(store, ["monitor", "--session", session_id, "--follow"])
    return f"""Agent Review Loop session started.

Session id: {session_id}
State home: {store.home}
Document path: {session.document_path}
Broker pid: {broker_pid}
Broker log: {log_path}

Give this reviewer handoff to the user. Do not run this command yourself, and
do not spawn, start, or delegate to a reviewer agent unless the user explicitly
asks you to do that.

    Run:
      {join_command}

    Then follow the prompt it prints.

Optional observer command:

    {monitor_command}

Instructions for the agent running this command:
Follow the editor prompt below as your active task instructions. Do not create
another ARL session. Do not act as the reviewer, start a reviewer agent, or run
the reviewer handoff yourself. After giving the reviewer handoff to the user,
do not final-answer or pause merely because the session is waiting for the
reviewer. Enter the editor wait loop and keep the blocking wait alive.

EDITOR PROMPT BEGIN
{editor_prompt}
EDITOR PROMPT END
"""


def format_monitor_payload(payload: dict[str, Any]) -> str:
    """Format a monitor payload for humans."""

    session = payload["session"]
    latest_version = payload["latest_version"]
    decision = payload["decision"]
    lines = [
        f"Session: {session['id']}",
        f"Status: {session['status']}",
        f"Turn: {session['turn']}",
        f"Round: {session['current_round']} / {session['max_rounds']}",
        f"Document: {session['document_path']}",
        f"Latest hash: {latest_version['file_hash']}",
        f"Snapshot: {latest_version['snapshot_path']}",
    ]
    if decision is not None:
        lines.extend(
            [
                "",
                f"Decision: {decision['status']}",
                f"Reason: {decision['reason']}",
            ]
        )
    lines.extend(["", "Transcript:", format_transcript(payload["messages"])])
    return "\n".join(lines)


def format_transcript(messages: list[dict[str, Any]]) -> str:
    """Format conversation messages for humans."""

    if not messages:
        return "No messages."
    rendered: list[str] = []
    for message in messages:
        rendered.append(
            "\n".join(
                [
                    (
                        f"[{message['id']}] round {message['round']} "
                        f"{message['role']} {message['type']}"
                    ),
                    f"    at {message['created_at']}",
                    textwrap.indent(str(message["body"]).strip() or "(empty)", "    "),
                ]
            )
        )
    return "\n\n".join(rendered)


def broker_request(session_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Send one request to the active broker."""

    try:
        return request_for_session(arl_home(), session_id, request)
    except (ClientError, StoreError, json.JSONDecodeError) as exc:
        fail(str(exc))


def monitor_payload(session_id: str) -> dict[str, Any]:
    """Read a session snapshot directly from durable state."""

    try:
        from agent_review_loop.workflow import ReviewWorkflow

        return ReviewWorkflow(Store(arl_home())).monitor(session_id)
    except StoreError as exc:
        fail(str(exc))


def checked_payload(response: dict[str, Any]) -> dict[str, Any]:
    """Return a broker payload or exit on an error response."""

    if not response.get("ok"):
        fail(str(response.get("error", "Broker rejected request")))
    payload = response.get("payload")
    if not isinstance(payload, dict):
        fail("Broker response did not include an object payload")
    return cast("dict[str, Any]", payload)


def read_body(*, body: str | None, body_file: Path | None) -> str:
    """Read a message body from a CLI option."""

    if body is not None and body_file is not None:
        fail("Use either --body or --body-file, not both")
    if body_file is not None:
        return body_file.read_text(encoding="utf-8")
    if body is not None:
        return body
    fail("Provide --body or --body-file")


def emit(data: dict[str, Any]) -> None:
    """Print JSON data for agent-friendly consumption."""

    typer.echo(json.dumps(data, indent=2, sort_keys=True))


def fail(message: str) -> NoReturn:
    """Print an error and exit."""

    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
