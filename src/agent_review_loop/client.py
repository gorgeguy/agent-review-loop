"""Client helpers for the local ARL broker."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from agent_review_loop.store import Store

JsonDict = dict[str, Any]


class ClientError(ConnectionError):
    """Raised when a broker request fails."""


def send_request(socket_path: Path, request: JsonDict) -> JsonDict:
    """Send one JSON request to the broker and return its response."""

    if not socket_path.exists():
        raise ClientError(f"Broker socket does not exist: {socket_path}")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(socket_path))
        client.sendall((json.dumps(request) + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data:
        raise ClientError("Broker returned an empty response")
    response = json.loads(data.decode("utf-8"))
    if not isinstance(response, dict):
        raise ClientError("Broker returned a non-object response")
    return response


def request_for_session(home: Path, session_id: str, request: JsonDict) -> JsonDict:
    """Send a request to the socket recorded for a session."""

    store = Store(home)
    session = store.get_session(session_id)
    return send_request(Path(session.socket_path), request)
