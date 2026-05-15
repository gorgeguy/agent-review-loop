"""Unix-domain JSONL broker for review sessions."""

from __future__ import annotations

import json
import signal
import socket
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_review_loop.models import TERMINAL_STATUSES
from agent_review_loop.store import Store, StoreError
from agent_review_loop.workflow import (
    ReviewWorkflow,
    WorkflowError,
    error_response,
    parse_message_type,
    parse_role,
    success_response,
)

JsonDict = dict[str, Any]

if TYPE_CHECKING:
    from collections.abc import Callable


class Broker:
    """Handle JSON requests for one ARL state store."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.workflow = ReviewWorkflow(store)

    def handle(self, request: JsonDict) -> JsonDict:
        """Handle one decoded JSON request."""

        try:
            action = str(request.get("action", ""))
            if action == "next":
                payload = self.workflow.next_action(
                    str(request["session_id"]),
                    role=parse_role(str(request["role"])),
                    context=int(request.get("context", 5)),
                    full_transcript=bool(request.get("full_transcript", False)),
                )
            elif action == "send":
                payload = self.workflow.send_message(
                    str(request["session_id"]),
                    role=parse_role(str(request["role"])),
                    message_type=parse_message_type(str(request["message_type"])),
                    body=str(request["body"]),
                )
            elif action == "monitor":
                payload = self.workflow.monitor(str(request["session_id"]))
            else:
                return error_response(f"Unknown action: {action}")
        except (KeyError, TypeError, ValueError, StoreError, WorkflowError) as exc:
            return error_response(str(exc))
        return success_response(payload)


def serve_session(
    session_id: str,
    *,
    home: Path,
    stop_event: threading.Event | None = None,
    install_signal_handlers: bool = True,
) -> None:
    """Serve one session until terminal state, SIGTERM, or stop_event."""

    store = Store(home)
    session = store.get_session(session_id)
    broker = Broker(store)
    sock_path = Path(session.socket_path)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    if sock_path.exists():
        sock_path.unlink()

    stopper = stop_event or threading.Event()
    previous_handlers: dict[int, Callable[[int, Any], None] | int | None] = {}
    if install_signal_handlers:
        previous_handlers = _install_signal_handlers(stopper)

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(sock_path))
            server.listen()
            server.settimeout(0.2)
            while not stopper.is_set():
                if store.get_session(session_id).status in TERMINAL_STATUSES:
                    break
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    response = _handle_connection(connection, broker)
                    connection.sendall((json.dumps(response) + "\n").encode("utf-8"))
    finally:
        if sock_path.exists():
            sock_path.unlink()
        if install_signal_handlers:
            _restore_signal_handlers(previous_handlers)


def _handle_connection(connection: socket.socket, broker: Broker) -> JsonDict:
    data = b""
    while not data.endswith(b"\n"):
        chunk = connection.recv(65536)
        if not chunk:
            break
        data += chunk
    if not data:
        return error_response("Empty request")
    try:
        request = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return error_response(f"Malformed JSON: {exc.msg}")
    if not isinstance(request, dict):
        return error_response("Request must be a JSON object")
    return broker.handle(request)


def _install_signal_handlers(
    stopper: threading.Event,
) -> dict[int, Callable[[int, Any], None] | int | None]:
    previous: dict[int, Callable[[int, Any], None] | int | None] = {}

    def handle_signal(signum: int, frame: Any) -> None:
        stopper.set()
        old_handler = previous.get(signum)
        if callable(old_handler):
            old_handler(signum, frame)

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, handle_signal)
    return previous


def _restore_signal_handlers(
    previous_handlers: dict[int, Callable[[int, Any], None] | int | None],
) -> None:
    for signum, handler in previous_handlers.items():
        signal.signal(signum, handler)
