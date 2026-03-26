"""
Preambulate — session-end Decision capture.

Called by Claude at the end of each session to record a summary
of choices made and files touched.

Usage:
    python decision.py \\
        --label "short summary of work done" \\
        --rationale "why key choices were made" \\
        --touched "path/a.py,path/b.md"

Environment variables (set by Claude Code):
    CLAUDE_PROJECT_DIR  — absolute path to the project root
    CLAUDE_SESSION_ID   — session identifier
"""

import argparse
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import kuzu


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

DEFAULT_DB_PATH = Path(
    os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).parent)
) / "memory.db"


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------
# Record
# ------------------------------------------------------------

def record_decision(
    db_path: Path,
    session_id: str,
    label: str,
    rationale: str,
    touched: List[str],
) -> None:
    if not db_path.exists():
        print(f"preambulate: no database at {db_path}, skipping")
        return

    db   = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    ts   = now()

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
    parser = argparse.ArgumentParser(description="Record a session-end Decision node.")
    parser.add_argument("--label",    required=True, help="One-line summary of work done")
    parser.add_argument("--rationale", required=True, help="Why key choices were made")
    parser.add_argument(
        "--touched",
        default="",
        help="Comma-separated relative paths of files edited this session",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--session-id",
        default=os.environ.get("CLAUDE_SESSION_ID") or new_id(),
    )
    args = parser.parse_args()

    touched = [p.strip() for p in args.touched.split(",") if p.strip()] if args.touched else []

    record_decision(
        db_path=args.db,
        session_id=args.session_id,
        label=args.label,
        rationale=args.rationale,
        touched=touched,
    )


if __name__ == "__main__":
    main()
