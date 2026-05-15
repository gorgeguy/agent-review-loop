"""Command line interface for Agent Review Loop."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, NoReturn, cast

import typer

from agent_review_loop.broker import serve_session
from agent_review_loop.client import ClientError, request_for_session
from agent_review_loop.paths import arl_home
from agent_review_loop.prompts import build_role_prompt
from agent_review_loop.store import Store, StoreError
from agent_review_loop.workflow import WorkflowError, parse_role

app = typer.Typer(no_args_is_help=True)


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
    while True:
        response = broker_request(session, request)
        payload = checked_payload(response)
        if not wait or payload["state"] != "waiting":
            emit(response)
            return
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
def transcript(session: str = typer.Option(..., "--session")) -> None:
    """Print the conversation transcript."""

    payload = monitor_payload(session)
    emit({"ok": True, "payload": {"messages": payload["messages"]}})


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
