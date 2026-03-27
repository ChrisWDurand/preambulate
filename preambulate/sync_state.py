"""
Preambulate — sync checkpoint state.

Tracks the last successful push/pull timestamps so incremental sync
knows which nodes and edges to include in the next push payload.

State file: <project_root>/.preambulate_sync_state.json
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


_STATE_FILE = ".preambulate_sync_state.json"


def _state_path(project_root: Path) -> Path:
    return project_root / _STATE_FILE


def load_sync_state(project_root: Path) -> dict:
    """Read the sync state file. Returns {} if absent or unreadable."""
    path = _state_path(project_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_sync_state(project_root: Path, state: dict) -> None:
    """Write the sync state file atomically (write to .tmp, then rename)."""
    path = _state_path(project_root)
    tmp  = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # non-fatal — next sync will just do a full push


def record_push(project_root: Path, status: str) -> None:
    """Update last_push_at and last_push_status in the state file."""
    state = load_sync_state(project_root)
    state["last_push_at"]     = datetime.now(timezone.utc).isoformat()
    state["last_push_status"] = status
    state.setdefault("schema_version", "2.0")
    save_sync_state(project_root, state)


def record_pull(project_root: Path, status: str) -> None:
    """Update last_pull_at and last_pull_status in the state file."""
    state = load_sync_state(project_root)
    state["last_pull_at"]     = datetime.now(timezone.utc).isoformat()
    state["last_pull_status"] = status
    state.setdefault("schema_version", "2.0")
    save_sync_state(project_root, state)


def get_last_push_dt(project_root: Path) -> datetime | None:
    """
    Return the last successful push timestamp as a UTC datetime, or None.
    Used by dump_since() to compute the incremental push window.
    """
    state = load_sync_state(project_root)
    raw   = state.get("last_push_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
