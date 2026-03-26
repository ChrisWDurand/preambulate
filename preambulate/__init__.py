"""preambulate — graph-based project memory for Claude Code."""

from __future__ import annotations

import os
from pathlib import Path


def get_project_dir() -> Path:
    """
    Resolve the project root directory.

    Priority:
    1. CLAUDE_PROJECT_DIR env var (always set by Claude Code hooks).
    2. Walk up from this file to find a directory containing pyproject.toml
       (works for editable installs: pipx install -e .).
    3. Current working directory (fallback for global installs and manual use).
    """
    if env := os.environ.get("CLAUDE_PROJECT_DIR"):
        return Path(env)
    here = Path(__file__).parent  # preambulate/ package dir
    for candidate in [here.parent, here.parent.parent]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


def get_db_path() -> Path:
    return get_project_dir() / "memory.db"
