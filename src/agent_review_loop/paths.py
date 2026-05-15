"""Filesystem locations for Agent Review Loop state."""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path


def arl_home() -> Path:
    """Return the state directory for the current command invocation."""

    configured = os.environ.get("ARL_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / ".arl").resolve()


def database_path(home: Path) -> Path:
    """Return the SQLite database path for a state directory."""

    return home / "arl.sqlite"


def snapshots_dir(home: Path) -> Path:
    """Return the content-addressed snapshot directory."""

    return home / "snapshots"


def sockets_dir(home: Path) -> Path:
    """Return the Unix socket directory."""

    home_hash = sha256(str(home).encode("utf-8")).hexdigest()[:12]
    return Path("/tmp/arl") / home_hash


def socket_path(home: Path, session_id: str) -> Path:
    """Return the Unix socket path for a session."""

    return sockets_dir(home) / f"{session_id}.sock"
