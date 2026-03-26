"""
Preambulate — artifact capture.

Fires via PostToolUse hook when Claude uses Write or Edit tools.
Creates an Artifact node for the file touched (if new), then
creates a Decision node recording the edit and anchors it.

Input: JSON on stdin from Claude Code PostToolUse hook.

Environment variables (set by Claude Code):
    CLAUDE_PROJECT_DIR  — absolute path to the project root
    CLAUDE_SESSION_ID   — session identifier
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import kuzu


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


# ------------------------------------------------------------
# Capture
# ------------------------------------------------------------

def capture_artifact(
    db_path: Path,
    file_path: str,
    session_id: str,
    tool_name: str,
) -> None:
    if not db_path.exists():
        return

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).parent))

    # Skip files outside the project
    try:
        rel_path = str(Path(file_path).relative_to(project_dir))
    except ValueError:
        return

    # Skip the DB itself
    if rel_path.startswith("memory.db"):
        return

    db   = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    ts   = now()

    # Check if an Artifact already exists for this path
    result = conn.execute(
        "MATCH (a:Artifact {path: $path}) RETURN a.id LIMIT 1",
        parameters={"path": rel_path},
    )
    artifact_id = None
    while result.has_next():
        artifact_id = result.get_next()[0]

    is_new      = artifact_id is None
    anchor_type = "created" if is_new else "modified"

    if is_new:
        artifact_id = new_id()
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
                "id":    artifact_id,
                "label": Path(file_path).name,
                "path":  rel_path,
                "kind":  infer_kind(file_path),
            },
        )

    # Create a Decision node for this edit
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
            "label":      f"{tool_name.lower()}:{Path(file_path).name}",
            "rationale":  f"{tool_name} applied to {rel_path}.",
            "timestamp":  ts,
            "session_id": session_id,
        },
    )

    # Anchor Decision -> Artifact
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
            "rationale":      f"Edit decision directly touched {rel_path}.",
            "anchor_type":    anchor_type,
        },
    )

    status = "new" if is_new else "updated"
    print(f"preambulate: artifact {status} [{Path(file_path).name}] ({rel_path})")


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return

    tool_name  = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    file_path  = tool_input.get("file_path", "")
    session_id = (
        payload.get("session_id")
        or os.environ.get("CLAUDE_SESSION_ID")
        or new_id()
    )

    if not file_path:
        return

    db_path = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).parent)) / "memory.db"
    capture_artifact(
        db_path=db_path,
        file_path=file_path,
        session_id=session_id,
        tool_name=tool_name,
    )


if __name__ == "__main__":
    main()
