"""
Preambulate — stable identity resolution.

Provides machine_id and author for Decision node attribution.
No external dependencies (stdlib only).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path


# Module-level cache — resolved once per process.
_author_cache: str | None = None


def get_machine_id(db_path: Path | None = None) -> str:
    """
    Return a stable per-project machine ID.

    Reads from <project_root>/.preambulate_id, creating a UUID file if absent.
    db_path is the memory.db path; project root is db_path.parent.

    Falls back to platform.node() when db_path is None or the file
    cannot be created (e.g. read-only filesystem).
    """
    if db_path is not None:
        id_file = db_path.parent / ".preambulate_id"
        try:
            if id_file.exists():
                mid = id_file.read_text(encoding="utf-8").strip()
                if mid:
                    return mid
            mid = str(uuid.uuid4())
            id_file.write_text(mid + "\n", encoding="utf-8")
            return mid
        except OSError:
            pass  # fall through to hostname fallback

    import platform
    return platform.node() or "unknown"


def get_author() -> str:
    """
    Return the current user identity string.

    Resolution order:
      1. git config user.email
      2. git config user.name
      3. USER / LOGNAME env var
      4. 'unknown'

    subprocess calls use a 2-second timeout to avoid blocking in
    CI or environments with no git config.
    """
    for git_key in ("user.email", "user.name"):
        try:
            result = subprocess.run(
                ["git", "config", "--get", git_key],
                capture_output=True,
                text=True,
                timeout=2,
            )
            value = result.stdout.strip()
            if value:
                return value
        except (OSError, subprocess.TimeoutExpired):
            pass

    return os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"


def author() -> str:
    """Return the cached author string (resolved once per process)."""
    global _author_cache
    if _author_cache is None:
        _author_cache = get_author()
    return _author_cache
