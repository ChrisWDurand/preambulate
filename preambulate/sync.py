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
from urllib.error import HTTPError, URLError

from preambulate import get_db_path, get_project_dir
from preambulate.export import dump_since, merge_remote
from preambulate.graph import open_graph
from preambulate.identity import get_machine_id
from preambulate.keystore import decrypt, encrypt, key_exists, load_api_key, replace_key, save_api_key
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


def _common_headers(db_path: Path, project: str, api_key: str) -> dict:
    return {
        "Authorization":           f"Bearer {api_key}",
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

    # Server does a full replace on every push — no server-side merge.
    # Always dump the full local graph so the remote stays complete.
    # The `full` flag and `since` are preserved for dry-run reporting only.
    since = None if full else get_last_push_dt(project_root)

    conn      = open_graph(db_path)
    data      = dump_since(conn, None)   # always full — server is a dumb store

    node_total = sum(len(v) for v in data["nodes"].values())
    edge_total = len(data["edges"])
    plaintext  = json.dumps(data).encode("utf-8")

    project_id = get_machine_id(db_path)
    if not key_exists(project_id):
        print("preambulate sync: push aborted — no encryption key (run 'preambulate init')")
        return
    payload = encrypt(project_id, plaintext)

    since_label = since.isoformat() if since else "beginning (full)"
    print(f"preambulate sync: push  project={project}")
    print(f"  endpoint : {endpoint}")
    print(f"  since    : {since_label}")
    print(f"  payload  : {len(payload):,} bytes encrypted  ({node_total} nodes, {edge_total} edges)")

    if dry_run:
        print("  (dry-run — nothing sent)")
        return

    MAX_PAYLOAD_BYTES = 10 * 1024 * 1024  # 10 MB — matches server limit
    if len(payload) > MAX_PAYLOAD_BYTES:
        print(
            f"preambulate sync: push aborted — payload {len(payload):,} bytes "
            f"exceeds {MAX_PAYLOAD_BYTES // 1024 // 1024} MB limit. "
            f"Use 'preambulate export dump' to inspect the graph."
        )
        return

    if not api_key:
        print("preambulate sync: PREAMBULATE_API_KEY not set — aborting")
        return

    headers = {**_common_headers(db_path, project, api_key), "Content-Type": "application/octet-stream"}
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
    except HTTPError as exc:
        if exc.code == 401:
            print("preambulate sync: push failed — invalid API key (run 'preambulate sync register' to get a new key)")
        elif exc.code == 402:
            print("preambulate sync: push failed — sync not authorized (visit preambulate.dev to activate your account)")
        elif exc.code == 409:
            try:
                body = json.loads(exc.read())
                print(f"preambulate sync: push failed — schema mismatch (server expects {body.get('expected', '?')})")
            except Exception:
                print("preambulate sync: push failed — schema version mismatch")
        elif exc.code == 413:
            try:
                body = json.loads(exc.read())
                limit_mb = body.get("max_bytes", 0) // 1024 // 1024
                print(f"preambulate sync: push failed — payload too large (server limit {limit_mb} MB)")
            except Exception:
                print("preambulate sync: push failed — payload too large")
        else:
            print(f"preambulate sync: push failed — HTTP {exc.code} {exc.reason}")
        record_push(project_root, "error")
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
        headers=_common_headers(db_path, project, api_key),
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except HTTPError as exc:
        if exc.code == 404:
            print(f"preambulate sync: no remote graph for '{project}' yet — skipping pull")
            return
        if exc.code == 401:
            print("preambulate sync: pull failed — invalid API key (run 'preambulate sync register' to get a new key)")
        elif exc.code == 402:
            print("preambulate sync: pull failed — sync not authorized (visit preambulate.dev to activate your account)")
        elif exc.code == 409:
            try:
                body = json.loads(exc.read())
                print(f"preambulate sync: pull failed — schema mismatch (server expects {body.get('expected', '?')})")
            except Exception:
                print("preambulate sync: pull failed — schema version mismatch")
        else:
            print(f"preambulate sync: pull failed — HTTP {exc.code} {exc.reason}")
        record_pull(project_root, "error")
        return
    except URLError as exc:
        print(f"preambulate sync: pull failed — {exc.reason}")
        record_pull(project_root, "error")
        return

    project_id = get_machine_id(db_path)
    if not key_exists(project_id):
        print("preambulate sync: pull aborted — no encryption key (run 'preambulate init')")
        record_pull(project_root, "error")
        return

    try:
        plaintext = decrypt(project_id, raw)
    except Exception as exc:
        print(f"preambulate sync: pull failed — decryption error ({exc})")
        record_pull(project_root, "error")
        return

    try:
        remote = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        print(f"preambulate sync: pull failed — invalid JSON after decryption ({exc})")
        record_pull(project_root, "error")
        return

    conn = open_graph(db_path)
    added, skipped, edges = merge_remote(conn, remote)

    print(f"preambulate sync: pull complete")
    print(f"  {added} nodes added, {skipped} nodes skipped, {edges} edges added")
    record_pull(project_root, "ok")


# ------------------------------------------------------------
# Register
# ------------------------------------------------------------

def _update_shell_exports(key: str) -> None:
    """Update PREAMBULATE_API_KEY in shell rc files that already export it."""
    candidates = [
        Path.home() / ".bashrc",
        Path.home() / ".bash_profile",
        Path.home() / ".profile",
        Path.home() / ".zshrc",
    ]
    updated = []
    for rc in candidates:
        if not rc.exists():
            continue
        text = rc.read_text()
        if "PREAMBULATE_API_KEY" not in text:
            continue
        import re
        new_text = re.sub(
            r"^(export\s+PREAMBULATE_API_KEY=).*$",
            rf"\g<1>{key}",
            text,
            flags=re.MULTILINE,
        )
        if new_text != text:
            rc.write_text(new_text)
            updated.append(rc.name)
    if updated:
        print(f"  updated {', '.join('~/' + f for f in updated)} — run 'source ~/{updated[0]}' to apply")


def _register() -> None:
    """Open the preambulate signup page to obtain or renew an API key."""
    url = "https://preambulate.dev"
    try:
        import webbrowser
        webbrowser.open(url)
        print(f"preambulate sync: opening {url}")
    except Exception:
        print(f"preambulate sync: visit {url}")
    print("  sign in with GitHub to receive a new API key")
    print("  to persist the key across all shells, run:")
    print("    preambulate sync save-key <your-key>")


# ------------------------------------------------------------
# Rotate
# ------------------------------------------------------------

def _rotate(db_path: Path, endpoint: str, api_key: str) -> None:
    """
    Rotate the API key and re-encrypt the remote graph with the new key.

    Steps:
      1. Pull and decrypt current graph into local db (ensures local is current)
      2. POST /keys/rotate → receive new API key
      3. Replace local key file with new key
      4. Push full graph re-encrypted with new key
    """
    if not api_key:
        print("preambulate sync: PREAMBULATE_API_KEY not set — aborting")
        return

    project_root = db_path.parent
    project_id   = get_machine_id(db_path)

    if not key_exists(project_id):
        print("preambulate sync: no encryption key found — run 'preambulate init' first")
        return

    # Step 1 — pull to make sure local is current before rotation
    print("preambulate sync: rotate — pulling current graph")
    _pull(db_path, endpoint, api_key, dry_run=False)

    # Step 2 — request key rotation
    rotate_url = endpoint.replace("/sync", "/keys/rotate")
    headers    = {**_common_headers(db_path, _project_name(project_root), api_key), "Content-Length": "0"}
    req = urllib_request.Request(
        url=rotate_url,
        data=b"",
        method="POST",
        headers=headers,
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except HTTPError as exc:
        print(f"preambulate sync: rotate failed — HTTP {exc.code} {exc.reason}")
        return
    except URLError as exc:
        print(f"preambulate sync: rotate failed — {exc.reason}")
        return

    new_api_key = body.get("key", "")
    if not new_api_key:
        print("preambulate sync: rotate failed — no key in response")
        return

    # Step 3 — replace local encryption key
    from cryptography.fernet import Fernet
    new_enc_key = Fernet.generate_key()
    replace_key(project_id, new_enc_key)
    print(f"  encryption key rotated: ~/.preambulate/{project_id}.key")

    # Step 4 — push full graph re-encrypted with new key
    print("preambulate sync: rotate — pushing re-encrypted graph with new API key")
    _push(db_path, endpoint, new_api_key, dry_run=False, full=True)

    print(f"preambulate sync: rotate complete")
    print(f"  new API key: {new_api_key}")
    print(f"  update PREAMBULATE_API_KEY in your environment")


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
        choices=["push", "pull", "rotate", "register", "save-key"],
        default="push",
        help="Operation: push (default), pull, rotate, register, or save-key <key>.",
    )
    parser.add_argument(
        "key_value",
        nargs="?",
        help="API key value for save-key operation.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("PREAMBULATE_ENDPOINT") or DEFAULT_ENDPOINT,
        help="Override the sync endpoint.",
    )
    parser.add_argument(
        "--api-key",
        default=load_api_key() or os.environ.get("PREAMBULATE_API_KEY"),
        help="API key (defaults to ~/.preambulate/api_key, then PREAMBULATE_API_KEY env var).",
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
    elif args.op == "pull":
        _pull(args.db, args.endpoint, args.api_key, args.dry_run)
    elif args.op == "register":
        _register()
    elif args.op == "save-key":
        if not args.key_value:
            print("preambulate sync: save-key requires a key value")
            print("  usage: preambulate sync save-key <your-key>")
        else:
            save_api_key(args.key_value)
            print(f"preambulate sync: API key saved to ~/.preambulate/api_key")
            _update_shell_exports(args.key_value)
            print("  key will be used automatically — no export needed")
    else:
        _rotate(args.db, args.endpoint, args.api_key)


if __name__ == "__main__":
    main()
