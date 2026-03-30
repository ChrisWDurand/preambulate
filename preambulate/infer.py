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

from preambulate import get_db_path, get_project_dir
from preambulate.graph import GraphConnection, open_graph


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

DEFAULT_ROOT    = get_project_dir()
DEFAULT_DB_PATH = get_db_path()

# Directories to never descend into during a full scan.
_SKIP_DIRS = {
    "__pycache__", ".git",
    "node_modules", ".mypy_cache", ".pytest_cache", ".tox",
    "site-packages",  # installed packages inside any venv
}
# Any path component starting with these prefixes is skipped.
# Catches: venv, .venv, venv310, .venv310, venv38, etc.
_SKIP_DIR_PREFIXES = ("venv", ".venv")
# Any path component ending with these suffixes is skipped.
_SKIP_DIR_SUFFIXES = (".egg-info", ".dist-info")


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
    for part in rel.parts:
        if part in _SKIP_DIRS:
            return True
        if part.startswith(_SKIP_DIR_PREFIXES):
            return True
        if part.endswith(_SKIP_DIR_SUFFIXES):
            return True
    return False


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


def _extract_import_maps(
    tree: ast.AST,
    file_path: Path,
    root: Path,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """
    Return two maps for cross-file call resolution:

    from_imports  — {rel_path: [name, ...]}
        Names brought into local scope via `from X import name`.
        Star imports recorded as ["*"] — treated as unresolvable.

    module_aliases — {local_alias: rel_path}
        Module references via `import X` or `import X as Y`.
        Enables resolution of qualified calls like `graph.open_graph()`.
    """
    from_imports:   dict[str, list[str]] = {}
    module_aliases: dict[str, str]       = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_absolute(alias.name, root)
                if not resolved:
                    continue
                try:
                    rel = str(resolved.relative_to(root))
                except ValueError:
                    continue
                local_name = alias.asname or alias.name.split(".")[0]
                module_aliases[local_name] = rel

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level  = node.level
            if level > 0:
                resolved = _resolve_relative(module, level, file_path, root)
            else:
                resolved = _resolve_absolute(module, root)
            if not resolved:
                continue
            try:
                rel = str(resolved.relative_to(root))
            except ValueError:
                continue
            for alias in node.names:
                name = alias.asname or alias.name
                from_imports.setdefault(rel, []).append(name)

    return from_imports, module_aliases


def _resolve_absolute(module_name: str, root: Path) -> Path | None:
    """
    Resolve a dotted module name to the most-specific project-local file.

    Tries progressively shorter suffixes so that `preambulate.graph` resolves
    to `preambulate/graph.py` rather than falling back to `preambulate/__init__.py`.
    """
    parts = module_name.split(".")
    if not parts[0]:
        return None
    for length in range(len(parts), 0, -1):
        base = root.joinpath(*parts[:length])
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
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
# Symbol extraction
# ------------------------------------------------------------

def _extract_symbols(tree: ast.AST) -> list[tuple[str, str]]:
    """
    Return (kind, name) for top-level and class-level function/class definitions.
    kind is one of: 'function', 'class', 'method'
    """
    symbols: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(("class", node.name))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(("method", f"{node.name}.{item.name}"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Only top-level functions (not methods — handled above)
            if not any(
                isinstance(parent, ast.ClassDef)
                for parent in ast.walk(tree)
                if any(child is node for child in ast.walk(parent))
                if parent is not tree
            ):
                symbols.append(("function", node.name))
    return symbols


def _extract_within_file_calls(
    tree: ast.AST,
    defined_names: set[str],
) -> list[tuple[str, str]]:
    """
    Return (caller_name, callee_name) pairs for calls within the same file.
    Only tracks calls to names defined in this file.
    """
    calls: list[tuple[str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        caller = node.name
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            # Direct call: foo()
            if isinstance(child.func, ast.Name) and child.func.id in defined_names:
                callee = child.func.id
                if callee != caller:
                    calls.append((caller, callee))
            # Attribute call: self.foo() — resolve to ClassName.foo if known
            elif isinstance(child.func, ast.Attribute):
                callee = child.func.attr
                if callee in defined_names and callee != caller:
                    calls.append((caller, callee))

    return calls


# ------------------------------------------------------------
# Cross-file call extraction
# ------------------------------------------------------------

def _extract_cross_file_calls(
    tree: ast.AST,
    defined_names: set[str],
    from_imports: dict[str, list[str]],
    module_aliases: dict[str, str],
    symbol_index: dict[str, str],
) -> list[tuple[str, str, str]]:
    """
    Return (caller_name, callee_name, callee_rel_path) for calls that cross
    module boundaries.  Within-file calls (callee in defined_names) are skipped.
    """
    # Reverse map: bare name → files that export it via `from X import name`
    name_to_files: dict[str, list[str]] = {}
    for rel_path, names in from_imports.items():
        for name in names:
            if name != "*":
                name_to_files.setdefault(name, []).append(rel_path)

    results: list[tuple[str, str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        caller_name = node.name

        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue

            callee_name: str | None = None
            callee_file: str | None = None

            if isinstance(child.func, ast.Name):
                name = child.func.id
                if name in defined_names:
                    continue
                sources = name_to_files.get(name, [])
                if len(sources) == 1:
                    callee_name = name
                    callee_file = sources[0]
                elif not sources and name in symbol_index:
                    callee_name = name
                    callee_file = symbol_index[name]

            elif isinstance(child.func, ast.Attribute):
                attr = child.func.attr
                if isinstance(child.func.value, ast.Name):
                    alias = child.func.value.id
                    if alias in module_aliases:
                        callee_name = attr
                        callee_file = module_aliases[alias]

            if callee_name and callee_file and callee_name != caller_name:
                results.append((caller_name, callee_name, callee_file))

    return results


def _build_symbol_index(conn: GraphConnection) -> dict[str, str]:
    """Return {base_symbol_name: rel_file_path} from all symbol Artifacts in the graph."""
    rows = conn.execute(
        "MATCH (a:Artifact) WHERE a.path CONTAINS '::' RETURN a.path"
    )
    index: dict[str, str] = {}
    for row in rows:
        path = row[0]
        if not path or "::" not in path:
            continue
        file_part, sym_part = path.split("::", 1)
        base = sym_part.split(".")[-1]
        if base not in index:  # first-write-wins on name collision
            index[base] = file_part
    return index


# ------------------------------------------------------------
# Graph writes
# ------------------------------------------------------------

def _symbol_path(file_rel: str, symbol_name: str) -> str:
    """Canonical path for a symbol artifact: 'file/path.py::SymbolName'."""
    return f"{file_rel}::{symbol_name}"


def _ensure_symbol_artifact(
    conn: GraphConnection,
    file_rel: str,
    symbol_name: str,
    kind: str,
) -> str:
    """Upsert an Artifact node for a symbol. Returns the symbol path."""
    sym_path = _symbol_path(file_rel, symbol_name)
    rows = conn.execute(
        "MATCH (a:Artifact {path: $path}) RETURN a.id LIMIT 1",
        parameters={"path": sym_path},
    )
    if not rows:
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
                "label": symbol_name,
                "path":  sym_path,
                "kind":  kind,
            },
        )
    return sym_path


def _ensure_governs(conn: GraphConnection, file_rel: str, sym_path: str) -> None:
    """Ensure a GOVERNS edge from the file artifact to the symbol artifact."""
    rows = conn.execute(
        """
        MATCH (f:Artifact {path: $file})-[r:GOVERNS]->(s:Artifact {path: $sym})
        RETURN COUNT(*)
        """,
        parameters={"file": file_rel, "sym": sym_path},
    )
    if rows and rows[0][0] > 0:
        return
    conn.execute(
        """
        MATCH (f:Artifact {path: $file}), (s:Artifact {path: $sym})
        CREATE (f)-[:GOVERNS {
            weight: $weight, traversal_cost: $traversal_cost,
            created_at: $created_at, rationale: $rationale
        }]->(s)
        """,
        parameters={
            "file":           file_rel,
            "sym":            sym_path,
            "weight":         1.0,
            "traversal_cost": 0.0,
            "created_at":     now(),
            "rationale":      f"Module {file_rel} defines {sym_path.split('::')[1]}.",
        },
    )


def _ensure_symbol_derives_from(
    conn: GraphConnection,
    caller_path: str,
    callee_path: str,
) -> bool:
    """Create a DERIVES_FROM edge between two symbol artifacts if absent."""
    rows = conn.execute(
        """
        MATCH (a:Artifact {path: $src})-[r:DERIVES_FROM]->(b:Artifact {path: $tgt})
        RETURN COUNT(*)
        """,
        parameters={"src": caller_path, "tgt": callee_path},
    )
    if rows and rows[0][0] > 0:
        return False
    conn.execute(
        """
        MATCH (a:Artifact {path: $src}), (b:Artifact {path: $tgt})
        CREATE (a)-[:DERIVES_FROM {
            weight: $weight, traversal_cost: $traversal_cost,
            created_at: $created_at, rationale: $rationale
        }]->(b)
        """,
        parameters={
            "src":            caller_path,
            "tgt":            callee_path,
            "weight":         1.0,
            "traversal_cost": 0.0,
            "created_at":     now(),
            "rationale":      (
                f"Inferred from call: "
                f"{caller_path.split('::')[-1]} calls {callee_path.split('::')[-1]}"
            ),
        },
    )
    return True


def _ensure_artifact(conn: GraphConnection, rel_path: str) -> None:
    """Create an Artifact node if one does not already exist for this path."""
    rows = conn.execute(
        "MATCH (a:Artifact {path: $path}) RETURN a.id LIMIT 1",
        parameters={"path": rel_path},
    )
    if rows:
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


def _ensure_derives_from(conn: GraphConnection, src: str, tgt: str) -> bool:
    """
    Create a DERIVES_FROM edge src → tgt if one does not already exist.
    Returns True if a new edge was created.
    """
    rows = conn.execute(
        """
        MATCH (a:Artifact {path: $src})-[r:DERIVES_FROM]->(b:Artifact {path: $tgt})
        RETURN COUNT(*) AS c
        """,
        parameters={"src": src, "tgt": tgt},
    )
    count = rows[0][0] if rows else 0
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

def infer_file(
    conn: GraphConnection,
    file_path: Path,
    root: Path,
    symbol_index: dict[str, str] | None = None,
) -> int:
    """
    Parse one Python file and write any new DERIVES_FROM edges.
    Returns the number of new edges created.

    symbol_index — {base_name: rel_file_path} built from existing symbol
    Artifacts in the graph.  When provided, Phase 4 (cross-file call
    resolution) runs.  Omit or pass None to skip Phase 4.
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

    # --- Phase 1: import-level DERIVES_FROM edges ---
    created = 0
    imported_paths = _extract_imports(tree, file_path, root)
    for imp_path in imported_paths:
        if _should_skip(imp_path, root):
            continue
        try:
            rel_tgt = str(imp_path.relative_to(root))
        except ValueError:
            continue
        if rel_src == rel_tgt:
            continue
        _ensure_artifact(conn, rel_tgt)
        if _ensure_derives_from(conn, rel_src, rel_tgt):
            print(
                f"preambulate: {Path(rel_src).name}"
                f" -[DERIVES_FROM]-> {Path(rel_tgt).name}"
            )
            created += 1

    # --- Phase 2: symbol extraction — file -[GOVERNS]-> symbol ---
    symbols = _extract_symbols(tree)
    defined_names: set[str] = set()
    for kind, name in symbols:
        sym_path = _ensure_symbol_artifact(conn, rel_src, name, kind)
        _ensure_governs(conn, rel_src, sym_path)
        defined_names.add(name.split(".")[-1])

    # Build sym_map once — used by both Phase 3 and Phase 4
    sym_map = {name.split(".")[-1]: _symbol_path(rel_src, name) for _, name in symbols}

    # --- Phase 3: within-file call resolution — caller -[DERIVES_FROM]-> callee ---
    if len(symbols) > 1:
        for caller_name, callee_name in _extract_within_file_calls(tree, defined_names):
            caller_path = sym_map.get(caller_name)
            callee_path = sym_map.get(callee_name)
            if caller_path and callee_path:
                if _ensure_symbol_derives_from(conn, caller_path, callee_path):
                    print(
                        f"preambulate: {caller_name}"
                        f" -[DERIVES_FROM]-> {callee_name}"
                        f"  ({Path(rel_src).name})"
                    )
                    created += 1

    # --- Phase 4: cross-file call resolution ---
    if symbol_index is not None and sym_map:
        from_imports, module_aliases = _extract_import_maps(tree, file_path, root)
        for caller_name, callee_name, callee_file in _extract_cross_file_calls(
            tree, defined_names, from_imports, module_aliases, symbol_index
        ):
            caller_path = sym_map.get(caller_name)
            if not caller_path:
                continue
            callee_sym_path = _symbol_path(callee_file, callee_name)
            rows = conn.execute(
                "MATCH (a:Artifact {path: $path}) RETURN a.id LIMIT 1",
                parameters={"path": callee_sym_path},
            )
            if not rows:
                continue
            if _ensure_symbol_derives_from(conn, caller_path, callee_sym_path):
                print(
                    f"preambulate: {caller_name}"
                    f" -[DERIVES_FROM]-> {callee_name}"
                    f"  ({Path(rel_src).name} → {Path(callee_file).name})"
                )
                created += 1

    return created


# ------------------------------------------------------------
# Full scan
# ------------------------------------------------------------

def infer_all(conn: GraphConnection, root: Path) -> int:
    symbol_index = _build_symbol_index(conn)
    total = 0
    for py_file in sorted(root.rglob("*.py")):
        if not _should_skip(py_file, root):
            total += infer_file(conn, py_file, root, symbol_index=symbol_index)
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

    hook_mode = False
    if file_path is None and not sys.stdin.isatty():
        hook_mode = True
        try:
            payload   = json.loads(sys.stdin.read())
            file_path = payload.get("tool_input", {}).get("file_path", "") or None
        except (json.JSONDecodeError, ValueError):
            file_path = None
        # In hook mode with no file path — exit silently, don't scan full project
        if file_path is None:
            return

    conn = open_graph(args.db)

    if file_path:
        symbol_index = _build_symbol_index(conn)
        infer_file(conn, Path(file_path), args.root, symbol_index=symbol_index)
    else:
        created = infer_all(conn, args.root)
        print(f"preambulate: import inference complete — {created} new edge(s)")


if __name__ == "__main__":
    main()
