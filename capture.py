"""
Preambulate — session capture.

Fires at Claude Code session start via the SessionStart hook.
Creates a Decision node for the session and anchors it to the
Seed node, giving the session a temporal address in the graph.

Environment variables (set by Claude Code):
    CLAUDE_PROJECT_DIR  — absolute path to the project root
    CLAUDE_SESSION_ID   — session identifier (if provided)

Usage (direct):
    python capture.py
    python capture.py --db ./memory.db --session-id <id>
"""

import argparse
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

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
# Capture
# ------------------------------------------------------------

def capture_session_start(db_path: Path, session_id: str) -> None:
    if not db_path.exists():
        print(f"preambulate: no database at {db_path}, skipping capture")
        return

    db   = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    ts = now()
    decision_id = new_id()

    # Write the Decision node
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
            "label":      "session_start",
            "rationale":  "Claude Code session initiated.",
            "timestamp":  ts,
            "session_id": session_id,
        },
    )

    # Anchor to the geometry Concept (seed-adjacent, depth 0)
    result = conn.execute("MATCH (c:Concept {label: 'geometry'}) RETURN c.id LIMIT 1")
    geometry_id = None
    while result.has_next():
        row = result.get_next()
        geometry_id = row[0]

    if geometry_id:
        conn.execute(
            """
            MATCH (d:Decision {id: $d_id}), (c:Concept {id: $c_id})
            CREATE (d)-[:ANCHORS {
                weight:         $weight,
                traversal_cost: $traversal_cost,
                created_at:     $created_at,
                rationale:      $rationale,
                anchor_type:    $anchor_type
            }]->(c)
            """,
            parameters={
                "d_id":          decision_id,
                "c_id":          geometry_id,
                "weight":        1.0,
                "traversal_cost": 0.0,
                "created_at":    ts,
                "rationale":     "Session start anchors to the graph root.",
                "anchor_type":   "discussed",
            },
        )

    print(f"preambulate: session captured [{session_id}] at {ts.isoformat()}")


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a Claude Code session start.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--session-id",
        default=os.environ.get("CLAUDE_SESSION_ID") or new_id(),
        help="Session identifier. Defaults to CLAUDE_SESSION_ID env var or a new UUID.",
    )
    args = parser.parse_args()

    capture_session_start(db_path=args.db, session_id=args.session_id)


if __name__ == "__main__":
    main()
