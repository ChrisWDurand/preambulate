"""
Preambulate — session-end Decision capture and semantic edge writer.

Usage
-----
Record a session-end Decision node (--label required):

    python decision.py \\
        --label "<one-line summary of work done>" \\
        --rationale "<why key choices were made>" \\
        --touched "<comma-separated relative paths of files edited>"

Write semantic edges after user confirmation (--label optional):

    python decision.py \\
        --concept "memory-briefing|Formatted graph query output shown at session start" \\
        --edge "briefing.py|INSTANTIATES|memory-briefing" \\
        --edge-rationale "briefing.py is the concrete implementation of the briefing pattern"

Combine both in one call:

    python decision.py \\
        --label "Add briefing module" \\
        --rationale "Decoupled query logic from capture.py" \\
        --touched "briefing.py,capture.py" \\
        --concept "memory-briefing|..." \\
        --edge "briefing.py|INSTANTIATES|memory-briefing" \\
        --edge-rationale "..."

Supported --edge relationship types: INSTANTIATES, DERIVES_FROM, RESONATES_WITH

Edge resolution: src/tgt strings are matched first against Artifact.path,
then against Concept.label.  A --concept entry must precede any --edge that
references it, or the concept must already exist in the graph.

RESONATES_WITH is undirected — both directions are written automatically.

Environment variables (set by Claude Code):
    CLAUDE_PROJECT_DIR  — absolute path to the project root
    CLAUDE_SESSION_ID   — session identifier
"""

from __future__ import annotations

import argparse
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import kuzu

from preambulate import get_db_path


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

DEFAULT_DB_PATH = get_db_path()

SUPPORTED_RELS = {"INSTANTIATES", "DERIVES_FROM", "RESONATES_WITH"}


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------
# Node resolution
# ------------------------------------------------------------

def _resolve_node(conn: kuzu.Connection, ref: str) -> Optional[tuple[str, str]]:
    """
    Resolve a string reference to (node_type, node_id).
    Checks Artifact.path first, then Concept.label.
    Returns None if not found.
    """
    r = conn.execute(
        "MATCH (a:Artifact {path: $ref}) RETURN a.id LIMIT 1",
        parameters={"ref": ref},
    )
    if r.has_next():
        return ("Artifact", r.get_next()[0])

    r = conn.execute(
        "MATCH (c:Concept {label: $ref}) RETURN c.id LIMIT 1",
        parameters={"ref": ref},
    )
    if r.has_next():
        return ("Concept", r.get_next()[0])

    return None


# ------------------------------------------------------------
# Concept upsert
# ------------------------------------------------------------

def ensure_concept(conn: kuzu.Connection, label: str, definition: str) -> str:
    """
    Ensure a Concept node exists for this label.  Creates it at depth 1
    if new.  Returns the concept ID.
    """
    r = conn.execute(
        "MATCH (c:Concept {label: $label}) RETURN c.id LIMIT 1",
        parameters={"label": label},
    )
    if r.has_next():
        cid = r.get_next()[0]
        print(f"preambulate: concept exists [{label}]")
        return cid

    cid = new_id()
    conn.execute(
        """
        CREATE (c:Concept {
            id:         $id,
            label:      $label,
            definition: $definition,
            depth:      $depth
        })
        """,
        parameters={
            "id":         cid,
            "label":      label,
            "definition": definition,
            "depth":      1,
        },
    )
    print(f"preambulate: concept created [{label}]")
    return cid


# ------------------------------------------------------------
# Semantic edge writer
# ------------------------------------------------------------

def write_edge(
    conn: kuzu.Connection,
    src_ref: str,
    rel: str,
    tgt_ref: str,
    rationale: str,
) -> None:
    """Write a single semantic edge.  RESONATES_WITH inserts both directions."""
    if rel not in SUPPORTED_RELS:
        print(f"preambulate: unsupported relationship {rel!r} — skipping")
        return

    src = _resolve_node(conn, src_ref)
    tgt = _resolve_node(conn, tgt_ref)

    if src is None:
        print(f"preambulate: source not found {src_ref!r} — skipping edge")
        return
    if tgt is None:
        print(f"preambulate: target not found {tgt_ref!r} — skipping edge")
        return

    src_type, src_id = src
    tgt_type, tgt_id = tgt
    ts = now()

    def _create(s_type, s_id, t_type, t_id) -> None:
        base = {
            "s_id":           s_id,
            "t_id":           t_id,
            "weight":         1.0,
            "traversal_cost": 0.0,
            "created_at":     ts,
            "rationale":      rationale,
        }
        if rel == "INSTANTIATES":
            conn.execute(
                f"""
                MATCH (s:{s_type} {{id: $s_id}}), (t:{t_type} {{id: $t_id}})
                CREATE (s)-[:INSTANTIATES {{
                    weight: $weight, traversal_cost: $traversal_cost,
                    created_at: $created_at, rationale: $rationale
                }}]->(t)
                """,
                parameters=base,
            )
        elif rel == "DERIVES_FROM":
            conn.execute(
                f"""
                MATCH (s:{s_type} {{id: $s_id}}), (t:{t_type} {{id: $t_id}})
                CREATE (s)-[:DERIVES_FROM {{
                    weight: $weight, traversal_cost: $traversal_cost,
                    created_at: $created_at, rationale: $rationale
                }}]->(t)
                """,
                parameters=base,
            )
        elif rel == "RESONATES_WITH":
            conn.execute(
                f"""
                MATCH (s:{s_type} {{id: $s_id}}), (t:{t_type} {{id: $t_id}})
                CREATE (s)-[:RESONATES_WITH {{
                    weight: $weight, traversal_cost: $traversal_cost,
                    created_at: $created_at, rationale: $rationale,
                    resonance_basis: $rationale
                }}]->(t)
                """,
                parameters=base,
            )

    _create(src_type, src_id, tgt_type, tgt_id)
    if rel == "RESONATES_WITH":
        _create(tgt_type, tgt_id, src_type, src_id)

    arrow = f"-[{rel}]->"
    print(f"preambulate: edge {src_ref} {arrow} {tgt_ref}")


# ------------------------------------------------------------
# Decision record
# ------------------------------------------------------------

def record_decision(
    conn: kuzu.Connection,
    session_id: str,
    label: str,
    rationale: str,
    touched: list[str],
) -> None:
    ts = now()
    decision_id = new_id()

    conn.execute(
        """
        CREATE (d:Decision {
            id:         $id,
            label:      $label,
            rationale:  $rationale,
            timestamp:  $timestamp,
            session_id: $session_id
        })
        """,
        parameters={
            "id":         decision_id,
            "label":      label,
            "rationale":  rationale,
            "timestamp":  ts,
            "session_id": session_id,
        },
    )

    anchored = []
    for rel_path in touched:
        rel_path = rel_path.strip()
        if not rel_path:
            continue

        result = conn.execute(
            "MATCH (a:Artifact {path: $path}) RETURN a.id LIMIT 1",
            parameters={"path": rel_path},
        )
        artifact_id = None
        while result.has_next():
            artifact_id = result.get_next()[0]

        if artifact_id is None:
            continue

        conn.execute(
            """
            MATCH (d:Decision {id: $d_id}), (a:Artifact {id: $a_id})
            CREATE (d)-[:ANCHORS {
                weight:         $weight,
                traversal_cost: $traversal_cost,
                created_at:     $created_at,
                rationale:      $rationale,
                anchor_type:    $anchor_type
            }]->(a)
            """,
            parameters={
                "d_id":           decision_id,
                "a_id":           artifact_id,
                "weight":         1.0,
                "traversal_cost": 0.0,
                "created_at":     ts,
                "rationale":      f"Session summary references {rel_path}.",
                "anchor_type":    "discussed",
            },
        )
        anchored.append(rel_path)

    summary = f"preambulate: decision recorded [{label}]"
    if anchored:
        summary += f" — anchored to: {', '.join(anchored)}"
    print(summary)


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record a session-end Decision and/or write semantic edges.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Decision args (optional as a group — all required together if --label given)
    parser.add_argument("--label",     default=None, help="One-line summary of work done")
    parser.add_argument("--rationale", default=None, help="Why key choices were made")
    parser.add_argument(
        "--touched",
        default="",
        help="Comma-separated relative paths of files edited this session",
    )

    # Semantic edge args
    parser.add_argument(
        "--concept",
        action="append",
        default=[],
        metavar="LABEL|DEFINITION",
        help=(
            "Ensure a Concept node exists. "
            "Format: 'label|definition'. Repeatable."
        ),
    )
    parser.add_argument(
        "--edge",
        action="append",
        default=[],
        metavar="SRC|REL|TGT",
        help=(
            "Write a semantic edge. "
            "Format: 'src|RELATIONSHIP|tgt'. Repeatable. "
            f"Supported: {', '.join(sorted(SUPPORTED_RELS))}."
        ),
    )
    parser.add_argument(
        "--edge-rationale",
        default="Confirmed semantic edge from prompted session-end suggestion.",
        help="Rationale applied to all --edge entries in this call.",
    )

    # Runtime
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--session-id",
        default=os.environ.get("CLAUDE_SESSION_ID") or new_id(),
    )

    args = parser.parse_args()

    # Validate: --label and --rationale must come together
    if (args.label is None) != (args.rationale is None):
        parser.error("--label and --rationale must be provided together")

    # Must do something
    if args.label is None and not args.concept and not args.edge:
        parser.error("Provide --label/--rationale, --concept, --edge, or a combination.")

    if not args.db.exists():
        print(f"preambulate: no database at {args.db}, skipping")
        return

    db   = kuzu.Database(str(args.db))
    conn = kuzu.Connection(db)

    # 1. Write Decision node
    if args.label is not None:
        touched = [p.strip() for p in args.touched.split(",") if p.strip()]
        record_decision(
            conn=conn,
            session_id=args.session_id,
            label=args.label,
            rationale=args.rationale,
            touched=touched,
        )

    # 2. Ensure concepts (must precede edges that reference them)
    for spec in args.concept:
        parts = spec.split("|", 1)
        if len(parts) != 2:
            print(
                f"preambulate: invalid --concept spec {spec!r} "
                "(expected 'label|definition') — skipping"
            )
            continue
        ensure_concept(conn, parts[0].strip(), parts[1].strip())

    # 3. Write edges
    for spec in args.edge:
        parts = [p.strip() for p in spec.split("|")]
        if len(parts) != 3:
            print(
                f"preambulate: invalid --edge spec {spec!r} "
                "(expected 'src|REL|tgt') — skipping"
            )
            continue
        write_edge(conn, parts[0], parts[1], parts[2], args.edge_rationale)


if __name__ == "__main__":
    main()
