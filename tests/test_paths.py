"""Filesystem path tests."""

from __future__ import annotations

from pathlib import Path

from agent_review_loop.paths import socket_path, sockets_dir


def test_socket_paths_should_use_short_tmp_root(tmp_path: Path) -> None:
    home = tmp_path / "state"

    sock_dir = sockets_dir(home)
    sock_path = socket_path(home, "arl-12345678")

    assert sock_dir.parent == Path("/tmp/arl")
    assert sock_dir.name
    assert len(sock_dir.name) == 12
    assert sock_path == sock_dir / "arl-12345678.sock"
