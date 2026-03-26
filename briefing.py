"""
Preambulate — memory briefing queries.

query_briefing(conn, current_session_id, focal_node=None)

    focal_node=None (recency mode)
        Returns the last N decisions and the artifacts touched across the
        last K sessions.  This is the default and is always available.

    focal_node=str (proximity mode)
        The string is interpreted as either a file path (matched against
        Artifact.path) or a concept label (matched against Concept.label).
        Returns the focal node, its 1-hop connections, and recent decisions
        that touched it — ordered by graph position rather than timestamp.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import kuzu


_FOOTER = "────────────────────────────────────────────────────────\n"

_RECENCY_DECISION_LIMIT = 5
_RECENCY_SESSION_LIMIT  = 3
_PROXIMITY_DECISION_LIMIT = 5


# Rationales written by automation — not worth surfacing in briefings.
_BORING_RATIONALES = {
    "Claude Code session initiated.",
}

def _is_boring(rationale: str | None) -> bool:
    if not rationale:
        return True
    if rationale in _BORING_RATIONALES:
        return True
    if rationale.startswith("Edit applied to "):
        return True
    if rationale.startswith("Session summary references "):
        return True
    if rationale.startswith("Inferred from Python import:"):
        return True
    return False


# ------------------------------------------------------------
# Public interface
# ------------------------------------------------------------

def query_briefing(
    conn: "kuzu.Connection",
    current_session_id: str,
    focal_node: str | None = None,
) -> list[str]:
    """
    Return briefing lines for the current session.

    Parameters
    ----------
    conn:
        Open Kuzu connection.
    current_session_id:
        The session that was just created — excluded from the 'prior
        sessions' artifact query so we don't surface our own writes.
    focal_node:
        None → recency mode.
        str  → proximity mode: file path or concept label.
    """
    if focal_node is not None:
        return _proximity_briefing(conn, focal_node)
    return _recency_briefing(conn, current_session_id)


def print_briefing(
    conn: "kuzu.Connection",
    current_session_id: str,
    focal_node: str | None = None,
) -> None:
    """Query and print the briefing to stdout."""
    lines = query_briefing(conn, current_session_id, focal_node=focal_node)
    print("\n".join(lines))


# ------------------------------------------------------------
# Recency mode
# ------------------------------------------------------------

def _recency_briefing(conn: "kuzu.Connection", current_session_id: str) -> list[str]:
    lines = ["\n── preambulate memory briefing ─────────────────────────────"]
    lines.extend(_recent_decisions(conn))
    lines.extend(_recent_artifacts(conn, current_session_id))
    lines.append(_FOOTER)
    return lines


def _recent_decisions(conn: "kuzu.Connection") -> list[str]:
    lines = ["\nRecent decisions:"]
    r = conn.execute(
        f"""
        MATCH (d:Decision)
        WHERE d.label <> 'session_start'
        RETURN d.label, d.rationale, d.timestamp, d.session_id
        ORDER BY d.timestamp DESC
        LIMIT {_RECENCY_DECISION_LIMIT}
        """,
    )
    found = False
    while r.has_next():
        found = True
        label, rationale, ts, sid = r.get_next()
        short_sid = (sid or "")[:8]
        lines.append(f"  [{short_sid}] {ts}  {label}")
        if not _is_boring(rationale):
            lines.append(f"    → {rationale}")
    if not found:
        lines.append("  (none yet)")
    return lines


def _recent_artifacts(conn: "kuzu.Connection", current_session_id: str) -> list[str]:
    lines = [f"\nArtifacts touched in last {_RECENCY_SESSION_LIMIT} sessions:"]

    r = conn.execute(
        f"""
        MATCH (d:Decision)
        WHERE d.session_id <> $current_session_id
        RETURN d.session_id, MAX(d.timestamp) AS last_ts
        ORDER BY last_ts DESC
        LIMIT {_RECENCY_SESSION_LIMIT}
        """,
        parameters={"current_session_id": current_session_id},
    )
    recent_sessions = []
    while r.has_next():
        sid, _ = r.get_next()
        if sid:
            recent_sessions.append(sid)

    if not recent_sessions:
        lines.append("  (no prior sessions)")
        return lines

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

    return lines


# ------------------------------------------------------------
# Proximity mode
# ------------------------------------------------------------

def _proximity_briefing(conn: "kuzu.Connection", focal_node: str) -> list[str]:
    node = _resolve_focal(conn, focal_node)
    if node is None:
        return [
            f"\n── preambulate memory briefing · focal: {focal_node} ─────",
            f"\n  not found: {focal_node!r} — no Artifact path or Concept label matches",
            _FOOTER,
        ]

    node_type, node_id, node_label, node_detail = node
    header = f"\n── preambulate memory briefing · focal: {node_label} ─────────"
    lines = [header, f"\nFocal: {node_label}  ({node_type} · {node_detail})"]
    lines.extend(_focal_connections(conn, node_type, node_id))
    lines.extend(_focal_decisions(conn, node_type, node_id))
    lines.append(_FOOTER)
    return lines


def _resolve_focal(
    conn: "kuzu.Connection",
    ref: str,
) -> tuple[str, str, str, str] | None:
    """
    Resolve a string to (node_type, node_id, display_label, detail).
    Tries Artifact.path first, then Concept.label.
    """
    r = conn.execute(
        "MATCH (a:Artifact {path: $ref}) RETURN a.id, a.label, a.kind LIMIT 1",
        parameters={"ref": ref},
    )
    if r.has_next():
        nid, label, kind = r.get_next()
        return ("Artifact", nid, label, kind)

    r = conn.execute(
        "MATCH (c:Concept {label: $ref}) RETURN c.id, c.label, c.definition LIMIT 1",
        parameters={"ref": ref},
    )
    if r.has_next():
        nid, label, definition = r.get_next()
        detail = (definition or "")[:60] or "no definition"
        return ("Concept", nid, label, detail)

    return None


def _focal_connections(
    conn: "kuzu.Connection",
    node_type: str,
    node_id: str,
) -> list[str]:
    """Return lines describing 1-hop connections from the focal node."""
    lines = ["\nConnected:"]
    rows: list[tuple[str, str, str, str]] = []  # (direction, rel, label, kind)

    def _collect(query: str) -> None:
        r = conn.execute(query, parameters={"id": node_id})
        while r.has_next():
            rows.append(tuple(r.get_next()))  # type: ignore[arg-type]

    if node_type == "Artifact":
        # Outbound: what this file imports / derives from
        _collect("""
            MATCH (src:Artifact {id: $id})-[:DERIVES_FROM]->(tgt:Artifact)
            RETURN '→' AS dir, 'DERIVES_FROM' AS rel, tgt.path AS label, 'Artifact' AS kind
        """)
        # Inbound: what imports this file
        _collect("""
            MATCH (src:Artifact)-[:DERIVES_FROM]->(tgt:Artifact {id: $id})
            RETURN '←' AS dir, 'DERIVES_FROM' AS rel, src.path AS label, 'Artifact' AS kind
        """)
        # Outbound: concepts this file instantiates
        _collect("""
            MATCH (src:Artifact {id: $id})-[:INSTANTIATES]->(tgt:Concept)
            RETURN '→' AS dir, 'INSTANTIATES' AS rel, tgt.label AS label, 'Concept' AS kind
        """)
        # Resonates with (stored as directed, represents undirected)
        _collect("""
            MATCH (src:Artifact {id: $id})-[:RESONATES_WITH]->(tgt:Artifact)
            RETURN '~' AS dir, 'RESONATES_WITH' AS rel, tgt.path AS label, 'Artifact' AS kind
        """)

    elif node_type == "Concept":
        # Inbound: files that instantiate this concept
        _collect("""
            MATCH (src:Artifact)-[:INSTANTIATES]->(tgt:Concept {id: $id})
            RETURN '←' AS dir, 'INSTANTIATES' AS rel, src.path AS label, 'Artifact' AS kind
        """)
        # Outbound: concepts this derives from
        _collect("""
            MATCH (src:Concept {id: $id})-[:DERIVES_FROM]->(tgt:Concept)
            RETURN '→' AS dir, 'DERIVES_FROM' AS rel, tgt.label AS label, 'Concept' AS kind
        """)
        # Inbound: concepts that derive from this one
        _collect("""
            MATCH (src:Concept)-[:DERIVES_FROM]->(tgt:Concept {id: $id})
            RETURN '←' AS dir, 'DERIVES_FROM' AS rel, src.label AS label, 'Concept' AS kind
        """)
        # Inbound GOVERNS (what governs this concept)
        _collect("""
            MATCH (src:Concept)-[:GOVERNS]->(tgt:Concept {id: $id})
            RETURN '←' AS dir, 'GOVERNS' AS rel, src.label AS label, 'Concept' AS kind
        """)
        # Outbound GOVERNS (what this concept governs)
        _collect("""
            MATCH (src:Concept {id: $id})-[:GOVERNS]->(tgt:Concept)
            RETURN '→' AS dir, 'GOVERNS' AS rel, tgt.label AS label, 'Concept' AS kind
        """)

    seen: set[tuple[str, str]] = set()
    found = False
    for direction, rel, label, kind in rows:
        key = (rel, label)
        if key in seen:
            continue
        seen.add(key)
        found = True
        lines.append(f"  {direction} [{rel}]  {label}  ({kind})")

    if not found:
        lines.append("  (no connections)")
    return lines


def _focal_decisions(
    conn: "kuzu.Connection",
    node_type: str,
    node_id: str,
) -> list[str]:
    """Return lines for recent Decisions that ANCHOR to the focal node."""
    lines = ["\nRecent decisions here:"]
    r = conn.execute(
        f"""
        MATCH (d:Decision)-[:ANCHORS]->(n:{node_type} {{id: $id}})
        WHERE d.label <> 'session_start'
        RETURN d.label, d.rationale, d.timestamp, d.session_id
        ORDER BY d.timestamp DESC
        LIMIT {_PROXIMITY_DECISION_LIMIT}
        """,
        parameters={"id": node_id},
    )
    found = False
    while r.has_next():
        found = True
        label, rationale, ts, sid = r.get_next()
        short_sid = (sid or "")[:8]
        lines.append(f"  [{short_sid}] {ts}  {label}")
        if not _is_boring(rationale):
            lines.append(f"    → {rationale}")
    if not found:
        lines.append("  (none)")
    return lines
