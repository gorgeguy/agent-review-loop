"""Tests for broker request handling."""

from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep

from agent_review_loop.broker import Broker, serve_session
from agent_review_loop.client import request_for_session
from agent_review_loop.store import Store


def test_broker_should_return_structured_error_for_malformed_action(tmp_path: Path) -> None:
    broker = Broker(Store(tmp_path / ".arl"))

    response = broker.handle({"action": "bogus"})

    assert response == {"ok": False, "error": "Unknown action: bogus"}


def test_broker_should_handle_next_and_send_requests(tmp_path: Path) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    store = Store(tmp_path / ".arl")
    session = store.create_session(document)
    broker = Broker(store)

    next_response = broker.handle(
        {"action": "next", "session_id": session.id, "role": "editor", "context": 5}
    )
    send_response = broker.handle(
        {
            "action": "send",
            "session_id": session.id,
            "role": "editor",
            "message_type": "review_request",
            "body": "Review my changes.",
        }
    )

    assert next_response["ok"] is True
    assert next_response["payload"]["state"] == "ready"
    assert send_response["ok"] is True
    assert send_response["payload"]["session"]["turn"] == "reviewer"


def test_server_should_listen_on_unix_socket_and_handle_jsonl_requests(tmp_path: Path) -> None:
    document = tmp_path / "draft.md"
    document.write_text("# Draft\n", encoding="utf-8")
    home = tmp_path / ".arl"
    store = Store(home)
    session = store.create_session(document)
    stop = Event()
    thread = Thread(
        target=serve_session,
        kwargs={
            "session_id": session.id,
            "home": home,
            "stop_event": stop,
            "install_signal_handlers": False,
        },
        daemon=True,
    )

    thread.start()
    try:
        wait_for_socket(Path(session.socket_path))
        response = request_for_session(
            home,
            session.id,
            {"action": "next", "session_id": session.id, "role": "editor"},
        )
    finally:
        stop.set()
        thread.join(timeout=2)

    assert response["ok"] is True
    assert response["payload"]["state"] == "ready"


def wait_for_socket(path: Path) -> None:
    deadline = monotonic() + 2
    while monotonic() < deadline:
        if path.exists():
            return
        sleep(0.01)
    raise AssertionError(f"socket did not appear: {path}")
