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
from pathlib import Path

from preambulate import get_db_path, get_project_dir
from preambulate.graph import open_graph
from preambulate.decision import DT_INFERRED, RS_CLAUDE_INFERRED, create_decision_node


def new_id() -> str:
    return str(uuid.uuid4())


def infer_kind(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in {".py", ".js", ".ts", ".go", ".rs", ".rb", ".java", ".c", ".cpp", ".h"}:
        return "module"
    if ext in {".md", ".txt", ".rst"}:
        return "document"
    return "file"


def capture_artifact(
    db_path: Path,
    file_path: str,
    session_id: str,
    tool_name: str,
) -> None:
    if not db_path.exists():
        return

    project_dir = get_project_dir()

    try:
        rel_path = str(Path(file_path).relative_to(project_dir))
    except ValueError:
        return

    if rel_path.startswith("memory.db"):
        return

    conn = open_graph(db_path)

    rows = conn.execute(
        "MATCH (a:Artifact {path: $path}) RETURN a.id LIMIT 1",
        parameters={"path": rel_path},
    )
    artifact_id = rows[0][0] if rows else None

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

    decision_id, ts = create_decision_node(
        conn, session_id,
        label=f"{tool_name.lower()}:{Path(file_path).name}",
        rationale=f"{tool_name} applied to {rel_path}.",
        decision_type=DT_INFERRED,
        rationale_source=RS_CLAUDE_INFERRED,
        db_path=db_path,
    )

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

    if is_new:
        print(f"preambulate: artifact new [{Path(file_path).name}] ({rel_path})")


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

    capture_artifact(
        db_path=get_db_path(),
        file_path=file_path,
        session_id=session_id,
        tool_name=tool_name,
    )


if __name__ == "__main__":
    main()
