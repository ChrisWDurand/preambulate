"""
Preambulate — sync command.

Packages memory.db and pushes it to api.preambulate.dev/sync.
All sync traffic routes through that single endpoint.

v1 backend: Cloudflare R2 (snapshot file storage).
v2 backend: Cloudflare Durable Objects (live shared graph).
Clients call the same endpoint; the backend is swapped transparently.

Usage:
    python sync.py push          # package and push local graph (default)
    python sync.py pull          # pull remote graph and replace local copy
    python sync.py               # defaults to push

    python sync.py push --dry-run           # show what would be sent
    python sync.py push --endpoint URL      # override endpoint (testing)

Authentication:
    Set PREAMBULATE_API_KEY in the environment.
    The key identifies the user and determines which project slot on the
    backend this graph belongs to.

Environment variables:
    PREAMBULATE_API_KEY     — required for push/pull (no-op if absent in dry-run)
    PREAMBULATE_ENDPOINT    — override the default endpoint
    CLAUDE_PROJECT_DIR      — project root (set by Claude Code hooks)
"""

from __future__ import annotations

import argparse
import io
import os
import platform
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import URLError

from preambulate import get_db_path, get_project_dir

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

DEFAULT_ENDPOINT = "https://api.preambulate.dev/sync"
DEFAULT_ROOT     = get_project_dir()
DEFAULT_DB_PATH  = get_db_path()


# ------------------------------------------------------------
# Packaging
# ------------------------------------------------------------

def _zip_db(db_path: Path) -> bytes:
    """
    Zip the memory.db directory into an in-memory bytes object.
    Kuzu databases are directories; we ship the whole thing.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(db_path.rglob("*")):
            if file.is_file():
                zf.write(file, arcname=file.relative_to(db_path.parent))
    return buf.getvalue()


def _unzip_db(data: bytes, db_path: Path) -> None:
    """Unzip a received snapshot over the local memory.db directory."""
    import shutil
    if db_path.exists():
        shutil.rmtree(db_path)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(db_path.parent)


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

def _project_name(root: Path) -> str:
    return root.resolve().name


def _machine_id() -> str:
    return platform.node() or "unknown"


def _push(
    db_path: Path,
    endpoint: str,
    api_key: str,
    dry_run: bool,
) -> None:
    if not db_path.exists():
        print(f"preambulate sync: no database at {db_path}")
        return

    payload  = _zip_db(db_path)
    ts       = datetime.now(timezone.utc).isoformat()
    project  = _project_name(db_path.parent)
    machine  = _machine_id()

    print(f"preambulate sync: push  project={project}  machine={machine}")
    print(f"  endpoint : {endpoint}")
    print(f"  payload  : {len(payload):,} bytes (zipped)")
    print(f"  timestamp: {ts}")

    if dry_run:
        print("  (dry-run — nothing sent)")
        return

    if not api_key:
        print("preambulate sync: PREAMBULATE_API_KEY not set — aborting")
        return

    req = urllib_request.Request(
        url=f"{endpoint}?op=push",
        data=payload,
        method="POST",
        headers={
            "Authorization":            f"Bearer {api_key}",
            "Content-Type":             "application/octet-stream",
            "X-Preambulate-Project":    project,
            "X-Preambulate-Machine":    machine,
            "X-Preambulate-Timestamp":  ts,
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            print(f"preambulate sync: push complete  status={resp.status}")
    except URLError as exc:
        print(f"preambulate sync: push failed — {exc.reason}")
        print("  (backend not yet live — this is expected for v1 development)")


def _pull(
    db_path: Path,
    endpoint: str,
    api_key: str,
    dry_run: bool,
) -> None:
    project = _project_name(db_path.parent)
    machine = _machine_id()

    print(f"preambulate sync: pull  project={project}  machine={machine}")
    print(f"  endpoint : {endpoint}")

    if dry_run:
        print("  (dry-run — nothing fetched)")
        return

    if not api_key:
        print("preambulate sync: PREAMBULATE_API_KEY not set — aborting")
        return

    req = urllib_request.Request(
        url=f"{endpoint}?op=pull&project={project}",
        method="GET",
        headers={
            "Authorization":         f"Bearer {api_key}",
            "X-Preambulate-Machine": machine,
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        _unzip_db(data, db_path)
        print(f"preambulate sync: pull complete  {len(data):,} bytes received")
    except URLError as exc:
        print(f"preambulate sync: pull failed — {exc.reason}")
        print("  (backend not yet live — this is expected for v1 development)")


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Push or pull the preambulate graph snapshot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "op",
        nargs="?",
        choices=["push", "pull"],
        default="push",
        help="Operation: push (default) or pull.",
    )
    parser.add_argument("--db",       type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--endpoint",
        default=(
            os.environ.get("PREAMBULATE_ENDPOINT") or DEFAULT_ENDPOINT
        ),
        help="Override the sync endpoint.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("PREAMBULATE_API_KEY", ""),
        help="API key (defaults to PREAMBULATE_API_KEY env var).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be sent without making any network calls.",
    )
    args = parser.parse_args()

    if args.op == "push":
        _push(args.db, args.endpoint, args.api_key, args.dry_run)
    else:
        _pull(args.db, args.endpoint, args.api_key, args.dry_run)


if __name__ == "__main__":
    main()
