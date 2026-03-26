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

    # Anchor Decision -> geometry Concept
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

    # ------------------------------------------------------------
    # Memory briefing
    # ------------------------------------------------------------
    _print_briefing(conn, session_id)


def _print_briefing(conn: kuzu.Connection, current_session_id: str) -> None:
    lines = ["\n── preambulate memory briefing ─────────────────────────────"]

    # Last 5 Decisions (excluding the session_start we just wrote)
    lines.append("\nRecent decisions:")
    r = conn.execute(
        """
        MATCH (d:Decision)
        WHERE d.label <> 'session_start'
        RETURN d.label, d.rationale, d.timestamp, d.session_id
        ORDER BY d.timestamp DESC
        LIMIT 5
        """
    )
    found = False
    while r.has_next():
        found = True
        label, rationale, ts, sid = r.get_next()
        short_sid = (sid or "")[:8]
        lines.append(f"  [{short_sid}] {ts}  {label}")
        if rationale and rationale != "Claude Code session initiated.":
            lines.append(f"    → {rationale}")
    if not found:
        lines.append("  (none yet)")

    # Artifacts touched in the last 3 sessions (excluding current)
    lines.append("\nArtifacts touched in last 3 sessions:")
    r = conn.execute(
        """
        MATCH (d:Decision)
        WHERE d.session_id <> $current_session_id
        RETURN d.session_id, MAX(d.timestamp) AS last_ts
        ORDER BY last_ts DESC
        LIMIT 3
        """,
        parameters={"current_session_id": current_session_id},
    )
    recent_sessions = []
    while r.has_next():
        sid, _ = r.get_next()
        if sid:
            recent_sessions.append(sid)

    if recent_sessions:
        r = conn.execute(
            """
            MATCH (d:Decision)-[:ANCHORS]->(a:Artifact)
            WHERE d.session_id IN $sids
            RETURN DISTINCT a.path, a.kind, d.session_id
            ORDER BY d.session_id, a.path
            """,
            parameters={"sids": recent_sessions},
        )
        found = False
        current_sid = None
        while r.has_next():
            found = True
            path, kind, sid = r.get_next()
            if sid != current_sid:
                current_sid = sid
                lines.append(f"  session {(sid or '')[:8]}:")
            lines.append(f"    {path}  ({kind})")
        if not found:
            lines.append("  (no file edits recorded)")
    else:
        lines.append("  (no prior sessions)")

    lines.append("────────────────────────────────────────────────────────\n")
    print("\n".join(lines))


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
