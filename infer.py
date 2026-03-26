"""
Preambulate — import inference.

Walks Python source files and creates DERIVES_FROM edges between
Artifact nodes based on import statements.  Only project-local imports
are tracked; stdlib and third-party packages are ignored.

Modes
-----
Full scan (run once or on demand):
    python infer.py
    python infer.py --root /path/to/project --db ./memory.db

Incremental via PostToolUse hook (JSON payload on stdin):
    <hook payload> | python infer.py

Direct file (testing / manual):
    python infer.py --file capture.py

Idempotent — safe to run multiple times.  Existing edges are not
duplicated and existing Artifact nodes are not overwritten.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import kuzu


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

DEFAULT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).parent))
DEFAULT_DB_PATH = DEFAULT_ROOT / "memory.db"

# Directories to never descend into during a full scan.
_SKIP_DIRS = {
    ".venv", "venv", "__pycache__", ".git",
    "node_modules", ".mypy_cache", ".pytest_cache", ".tox",
}


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


def infer_kind(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in {".py", ".js", ".ts", ".go", ".rs", ".rb", ".java", ".c", ".cpp", ".h"}:
        return "module"
    if ext in {".md", ".txt", ".rst"}:
        return "document"
    return "file"


def _should_skip(file_path: Path, root: Path) -> bool:
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        return True
    return any(part in _SKIP_DIRS for part in rel.parts)


# ------------------------------------------------------------
# Import extraction
# ------------------------------------------------------------

def _extract_imports(tree: ast.AST, file_path: Path, root: Path) -> list[Path]:
    """Return absolute paths of project-local files imported by this module."""
    results: list[Path] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_absolute(alias.name, root)
                if resolved:
                    results.append(resolved)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level  = node.level  # 0 = absolute, ≥1 = relative
            if level > 0:
                resolved = _resolve_relative(module, level, file_path, root)
            else:
                resolved = _resolve_absolute(module, root)
            if resolved:
                results.append(resolved)
    return results


def _resolve_absolute(module_name: str, root: Path) -> Path | None:
    """Resolve a dotted module name to a project-local file, or None."""
    top = module_name.split(".")[0]
    if not top:
        return None
    for candidate in (root / f"{top}.py", root / top / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _resolve_relative(module: str, level: int, file_path: Path, root: Path) -> Path | None:
    """Resolve a relative import (level ≥ 1) to a project-local file, or None."""
    base = file_path.parent
    for _ in range(level - 1):
        base = base.parent
        if not base.is_relative_to(root):
            return None

    if module:
        top = module.split(".")[0]
        for candidate in (base / f"{top}.py", base / top / "__init__.py"):
            if candidate.exists():
                return candidate
    else:
        candidate = base / "__init__.py"
        if candidate.exists():
            return candidate
    return None


# ------------------------------------------------------------
# Graph writes
# ------------------------------------------------------------

def _ensure_artifact(conn: kuzu.Connection, rel_path: str) -> None:
    """Create an Artifact node if one does not already exist for this path."""
    r = conn.execute(
        "MATCH (a:Artifact {path: $path}) RETURN a.id LIMIT 1",
        parameters={"path": rel_path},
    )
    if r.has_next():
        return
    conn.execute(
        """
        CREATE (a:Artifact {
            id:    $id,
            label: $label,
            path:  $path,
            kind:  $kind
        })
        """,
        parameters={
            "id":    new_id(),
            "label": Path(rel_path).name,
            "path":  rel_path,
            "kind":  infer_kind(rel_path),
        },
    )


def _ensure_derives_from(conn: kuzu.Connection, src: str, tgt: str) -> bool:
    """
    Create a DERIVES_FROM edge src → tgt if one does not already exist.
    Returns True if a new edge was created.
    """
    r = conn.execute(
        """
        MATCH (a:Artifact {path: $src})-[r:DERIVES_FROM]->(b:Artifact {path: $tgt})
        RETURN COUNT(*) AS c
        """,
        parameters={"src": src, "tgt": tgt},
    )
    count = r.get_next()[0] if r.has_next() else 0
    if count > 0:
        return False

    conn.execute(
        """
        MATCH (a:Artifact {path: $src}), (b:Artifact {path: $tgt})
        CREATE (a)-[:DERIVES_FROM {
            weight:         $weight,
            traversal_cost: $traversal_cost,
            created_at:     $created_at,
            rationale:      $rationale
        }]->(b)
        """,
        parameters={
            "src":            src,
            "tgt":            tgt,
            "weight":         1.0,
            "traversal_cost": 0.0,
            "created_at":     now(),
            "rationale":      (
                f"Inferred from Python import: "
                f"{Path(src).name} imports {Path(tgt).name}"
            ),
        },
    )
    return True


# ------------------------------------------------------------
# Per-file inference
# ------------------------------------------------------------

def infer_file(conn: kuzu.Connection, file_path: Path, root: Path) -> int:
    """
    Parse one Python file and write any new DERIVES_FROM edges.
    Returns the number of new edges created.
    """
    if file_path.suffix.lower() != ".py":
        return 0
    if _should_skip(file_path, root):
        return 0

    try:
        rel_src = str(file_path.relative_to(root))
    except ValueError:
        return 0

    try:
        source = file_path.read_text(encoding="utf-8")
        tree   = ast.parse(source, filename=str(file_path))
    except (SyntaxError, OSError):
        return 0

    # Ensure the source artifact exists regardless of whether it has local imports
    _ensure_artifact(conn, rel_src)

    imported_paths = _extract_imports(tree, file_path, root)
    if not imported_paths:
        return 0

    created = 0
    for imp_path in imported_paths:
        if _should_skip(imp_path, root):
            continue
        try:
            rel_tgt = str(imp_path.relative_to(root))
        except ValueError:
            continue

        # Don't record self-imports (shouldn't happen, but guard anyway)
        if rel_src == rel_tgt:
            continue

        _ensure_artifact(conn, rel_tgt)
        if _ensure_derives_from(conn, rel_src, rel_tgt):
            print(
                f"preambulate: {Path(rel_src).name}"
                f" -[DERIVES_FROM]-> {Path(rel_tgt).name}"
            )
            created += 1

    return created


# ------------------------------------------------------------
# Full scan
# ------------------------------------------------------------

def infer_all(conn: kuzu.Connection, root: Path) -> int:
    total = 0
    for py_file in sorted(root.rglob("*.py")):
        if not _should_skip(py_file, root):
            total += infer_file(conn, py_file, root)
    return total


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Infer DERIVES_FROM edges from Python import statements."
    )
    parser.add_argument("--db",   type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Process a single file instead of scanning the whole project.",
    )
    args = parser.parse_args()

    if not args.db.exists():
        return

    # Determine target file:
    #   1. --file flag (direct invocation or testing)
    #   2. stdin JSON payload (PostToolUse hook)
    #   3. None → full scan
    file_path: str | None = args.file

    if file_path is None and not sys.stdin.isatty():
        try:
            payload   = json.loads(sys.stdin.read())
            file_path = payload.get("tool_input", {}).get("file_path", "") or None
        except (json.JSONDecodeError, ValueError):
            file_path = None

    db   = kuzu.Database(str(args.db))
    conn = kuzu.Connection(db)

    if file_path:
        created = infer_file(conn, Path(file_path), args.root)
        if created == 0:
            pass  # nothing to report — no new edges
    else:
        created = infer_all(conn, args.root)
        print(f"preambulate: import inference complete — {created} new edge(s)")


if __name__ == "__main__":
    main()
