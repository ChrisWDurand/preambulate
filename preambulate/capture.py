"""
Preambulate — session capture.

Fires at Claude Code session start via the SessionStart hook.
Creates a Decision node for the session and anchors it to the
seed geometry, giving the session a temporal address in the graph.

Environment variables (set by Claude Code):
    CLAUDE_PROJECT_DIR  — absolute path to the project root
    CLAUDE_SESSION_ID   — session identifier (if provided)

Usage:
    preambulate capture
    preambulate capture --db ./memory.db --session-id <id>
"""

import argparse
import os
import uuid
from pathlib import Path

import kuzu

from preambulate import get_db_path
from preambulate.briefing import print_briefing
from preambulate.init import init as init_db
from preambulate.decision import (
    DT_AUTONOMOUS,
    RS_CLAUDE_INFERRED,
    create_decision_node,
)


def new_id() -> str:
    return str(uuid.uuid4())


def capture_session_start(db_path: Path, session_id: str) -> None:
    if not db_path.exists():
        print(f"preambulate: no database at {db_path}, running init...")
        init_db(db_path=db_path)

    db   = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    decision_id, ts = create_decision_node(
        conn, session_id,
        label="session_start",
        rationale="Claude Code session initiated.",
        decision_type=DT_AUTONOMOUS,
        rationale_source=RS_CLAUDE_INFERRED,
        db_path=db_path,
    )

    conn.execute(
        """
        MATCH (d:Decision {id: $d_id}), (c:Concept {label: 'geometry'})
        CREATE (d)-[:ANCHORS {
            weight:         $weight,
            traversal_cost: $traversal_cost,
            created_at:     $created_at,
            rationale:      $rationale,
            anchor_type:    $anchor_type
        }]->(c)
        """,
        parameters={
            "d_id":           decision_id,
            "weight":         1.0,
            "traversal_cost": 0.0,
            "created_at":     ts,
            "rationale":      "Session start anchored to seed geometry.",
            "anchor_type":    "discussed",
        },
    )

    print(f"preambulate: session captured [{session_id}] at {ts.isoformat()}")
    print_briefing(conn, session_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a Claude Code session start.")
    parser.add_argument("--db", type=Path, default=get_db_path())
    parser.add_argument(
        "--session-id",
        default=os.environ.get("CLAUDE_SESSION_ID") or new_id(),
    )
    args = parser.parse_args()
    capture_session_start(db_path=args.db, session_id=args.session_id)


if __name__ == "__main__":
    main()
