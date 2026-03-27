"""
Preambulate — sync command.

Pushes/pulls the local graph to/from api.preambulate.dev/sync using
a JSON export format that enables per-node merge on pull.

Sync model: periodic/intermittent. Live sync is a future paid feature.
Push sends only nodes/edges created since the last successful push
(incremental). Pull merges the remote graph into the local one
(accept new nodes/edges, local wins on UUID conflict — conservative
Stage 1/2 policy; LWW upgrade lives in export.merge_remote()).

Usage:
    preambulate sync push          # incremental push (default)
    preambulate sync pull          # merge remote into local
    preambulate sync               # defaults to push

    preambulate sync push --dry-run           # show payload size, nothing sent
    preambulate sync push --endpoint URL      # override endpoint (testing)
    preambulate sync push --full              # force full dump regardless of checkpoint

Authentication:
    Set PREAMBULATE_API_KEY in the environment.

Environment variables:
    PREAMBULATE_API_KEY     — required for push/pull (no-op if absent in dry-run)
    PREAMBULATE_ENDPOINT    — override the default endpoint
    CLAUDE_PROJECT_DIR      — project root (set by Claude Code hooks)
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import URLError

import kuzu

from preambulate import get_db_path, get_project_dir
from preambulate.export import dump_since, merge_remote
from preambulate.identity import get_machine_id
from preambulate.sync_state import get_last_push_dt, record_pull, record_push


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

DEFAULT_ENDPOINT = "https://api.preambulate.dev/sync"
DEFAULT_ROOT     = get_project_dir()
DEFAULT_DB_PATH  = get_db_path()


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _project_name(root: Path) -> str:
    return root.resolve().name


def _common_headers(db_path: Path, project: str) -> dict:
    return {
        "Authorization":           f"Bearer {os.environ.get('PREAMBULATE_API_KEY', '')}",
        "X-Preambulate-Project":   project,
        "X-Preambulate-Machine":   get_machine_id(db_path),
        "X-Preambulate-Timestamp": datetime.now(timezone.utc).isoformat(),
        "X-Preambulate-Schema":    "2.0",
        "User-Agent":              "preambulate-client/1.0",
    }


# ------------------------------------------------------------
# Push
# ------------------------------------------------------------

def _push(
    db_path: Path,
    endpoint: str,
    api_key: str,
    dry_run: bool,
    full: bool,
) -> None:
    if not db_path.exists():
        print(f"preambulate sync: no database at {db_path}")
        return

    project_root = db_path.parent
    project      = _project_name(project_root)

    since = None if full else get_last_push_dt(project_root)

    db   = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    data = dump_since(conn, since)

    node_total = sum(len(v) for v in data["nodes"].values())
    edge_total = len(data["edges"])
    payload    = json.dumps(data).encode("utf-8")

    since_label = since.isoformat() if since else "beginning (full)"
    print(f"preambulate sync: push  project={project}")
    print(f"  endpoint : {endpoint}")
    print(f"  since    : {since_label}")
    print(f"  payload  : {len(payload):,} bytes  ({node_total} nodes, {edge_total} edges)")

    if dry_run:
        print("  (dry-run — nothing sent)")
        return

    if not api_key:
        print("preambulate sync: PREAMBULATE_API_KEY not set — aborting")
        return

    headers = {**_common_headers(db_path, project), "Content-Type": "application/json"}
    req = urllib_request.Request(
        url=f"{endpoint}?op=push",
        data=payload,
        method="POST",
        headers=headers,
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            print(f"preambulate sync: push complete  status={resp.status}")
        record_push(project_root, "ok")
    except URLError as exc:
        print(f"preambulate sync: push failed — {exc.reason}")
        record_push(project_root, "error")


# ------------------------------------------------------------
# Pull
# ------------------------------------------------------------

def _pull(
    db_path: Path,
    endpoint: str,
    api_key: str,
    dry_run: bool,
) -> None:
    if not db_path.exists():
        print(f"preambulate sync: no database at {db_path}")
        return

    project_root = db_path.parent
    project      = _project_name(project_root)

    print(f"preambulate sync: pull  project={project}")
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
        headers=_common_headers(db_path, project),
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except URLError as exc:
        print(f"preambulate sync: pull failed — {exc.reason}")
        record_pull(project_root, "error")
        return

    try:
        remote = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"preambulate sync: pull failed — invalid JSON response ({exc})")
        record_pull(project_root, "error")
        return

    db   = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    added, skipped, edges = merge_remote(conn, remote)

    print(f"preambulate sync: pull complete")
    print(f"  {added} nodes added, {skipped} nodes skipped, {edges} edges added")
    record_pull(project_root, "ok")


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Push or pull the preambulate graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "op",
        nargs="?",
        choices=["push", "pull"],
        default="push",
        help="Operation: push (default) or pull.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("PREAMBULATE_ENDPOINT") or DEFAULT_ENDPOINT,
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
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force a full dump regardless of sync checkpoint (push only).",
    )
    args = parser.parse_args()

    if args.op == "push":
        _push(args.db, args.endpoint, args.api_key, args.dry_run, args.full)
    else:
        _pull(args.db, args.endpoint, args.api_key, args.dry_run)


if __name__ == "__main__":
    main()
