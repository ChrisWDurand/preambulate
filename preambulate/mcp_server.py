"""
Preambulate — MCP server.

Exposes preambulate graph memory as MCP tools.  The server instructions
tell Claude to treat the session briefing and git state as first-class
context, so behavioral guidance ships with the package rather than
living in the user's CLAUDE.md.

Entry point: preambulate mcp
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from preambulate import get_db_path
from preambulate.graph import GraphConnection, open_graph
from preambulate.briefing import query_briefing
from preambulate.decision import ensure_concept, new_id, record_decision, write_edge


_INSTRUCTIONS = (
    "You are connected to preambulate, a graph-based project memory system. "
    "The graph records every session, file edit, and decision made in this project — "
    "with rationale. It is your primary source of context. Use it before asking questions.\n\n"

    "SESSION START — call briefing() immediately. "
    "The recency briefing (no focal_node) shows recently touched files and the decisions behind them. "
    "Treat it as sufficient context to resume work. "
    "If the task involves a specific file or concept, call briefing(focal_node=...) for proximity mode — "
    "it shows the decision history and graph neighborhood around that node. "
    "Check pending proposals at the top of the briefing — AGREED proposals need to be acted on. "
    "Do not ask clarifying questions that the briefing already answers.\n\n"

    "DURING WORK — use query_artifacts() to locate files you haven't read yet. "
    "The graph narrows the search to the relevant region; it does not replace reading code. "
    "After identifying the region via briefing or query_artifacts, read the actual files. "
    "Do not infer correctness or completeness from graph entries alone.\n\n"

    "SESSION END — record a Decision node before your final message. "
    "label: one-line summary of what was done. "
    "rationale: why the key choices were made — not what changed, but why. "
    "touched: relative paths of files edited this session. "
    "Then propose 1–2 semantic edges if anything earned its place in the graph. "
    "Edges must be concrete and carry traversal value — they should shorten the path "
    "from a concept to the relevant artifact, or make a governance relationship explicit. "
    "Every edge needs a rationale. Decorative edges are not written. "
    "Present edge suggestions to the user before calling record_decision with them.\n\n"

    "EDGE TYPES to consider: "
    "INSTANTIATES (file is the concrete implementation of a concept), "
    "DERIVES_FROM (file or concept logically descends from another, beyond imports), "
    "RESONATES_WITH (two nodes occupy structurally similar positions in the graph), "
    "GOVERNS (source structure shapes how the target is traversed or interpreted).\n\n"

    "INVARIANT — every edge has a rationale. An edge without a rationale is noise, not memory. "
    "Skip the edge step entirely if nothing earned its place this session."
)

server = Server("preambulate", instructions=_INSTRUCTIONS)


# ------------------------------------------------------------
# DB helper
# ------------------------------------------------------------

def _open_conn() -> GraphConnection | None:
    db_path = get_db_path()
    if not db_path.exists():
        return None
    return open_graph(db_path)


# ------------------------------------------------------------
# Tools
# ------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="briefing",
            description=(
                "Query the preambulate memory briefing. "
                "Returns recently active files and the decisions behind them. "
                "Pass focal_node (file path or concept label) to switch to proximity mode."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "focal_node": {
                        "type": "string",
                        "description": (
                            "File path or concept label for proximity mode. "
                            "Omit for recency mode."
                        ),
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Current session ID to exclude from recency results.",
                    },
                },
            },
        ),
        Tool(
            name="record_decision",
            description=(
                "Record a Decision node and optional semantic edges. "
                "Call when work is complete: supply label, rationale, touched files, "
                "and any edges earned this session."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "One-line summary of what was done.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why the key choices were made.",
                    },
                    "touched": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Relative file paths edited this session.",
                    },
                    "concepts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Concepts to ensure exist before writing edges. "
                            "Format: 'label|definition'."
                        ),
                    },
                    "edges": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Semantic edges to write. "
                            "Format: 'src|REL|tgt'. "
                            "Supported: INSTANTIATES, DERIVES_FROM, RESONATES_WITH."
                        ),
                    },
                    "edge_rationale": {
                        "type": "string",
                        "description": "Rationale applied to all edges in this call.",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Session ID for the decision node.",
                    },
                },
                "required": ["label", "rationale"],
            },
        ),
        Tool(
            name="query_artifacts",
            description=(
                "Query artifacts recorded in the graph. "
                "Pass path to filter by file path substring; "
                "omit to list the most recently touched artifacts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Substring to match against artifact paths.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return. Default 20.",
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    conn = _open_conn()
    if conn is None:
        return [TextContent(
            type="text",
            text="preambulate: no database — run `preambulate init` first",
        )]

    if name == "briefing":
        focal_node = arguments.get("focal_node")
        session_id = (
            arguments.get("session_id")
            or os.environ.get("CLAUDE_SESSION_ID")
            or ""
        )
        lines = query_briefing(conn, session_id, focal_node=focal_node)
        return [TextContent(type="text", text="\n".join(lines))]

    if name == "record_decision":
        session_id = (
            arguments.get("session_id")
            or os.environ.get("CLAUDE_SESSION_ID")
            or new_id()
        )
        label          = arguments["label"]
        rationale      = arguments["rationale"]
        touched        = arguments.get("touched") or []
        concepts       = arguments.get("concepts") or []
        edges          = arguments.get("edges") or []
        edge_rationale = arguments.get("edge_rationale") or "Confirmed semantic edge."

        msgs: list[str] = []

        record_decision(conn, session_id, label, rationale, touched)
        msgs.append(f"decision recorded: {label}")

        for spec in concepts:
            parts = spec.split("|", 1)
            if len(parts) == 2:
                ensure_concept(conn, parts[0].strip(), parts[1].strip())
                msgs.append(f"concept ensured: {parts[0].strip()}")
            else:
                msgs.append(f"skipped malformed concept spec: {spec!r}")

        for spec in edges:
            parts = [p.strip() for p in spec.split("|")]
            if len(parts) == 3:
                write_edge(conn, parts[0], parts[1], parts[2], edge_rationale)
                msgs.append(f"edge written: {parts[0]} -[{parts[1]}]-> {parts[2]}")
            else:
                msgs.append(f"skipped malformed edge spec: {spec!r}")

        return [TextContent(type="text", text="\n".join(msgs))]

    if name == "query_artifacts":
        path_filter = arguments.get("path")
        limit       = int(arguments.get("limit") or 20)

        if path_filter:
            r = conn.execute(
                """
                MATCH (a:Artifact)
                WHERE a.path CONTAINS $path
                RETURN a.path, a.kind
                ORDER BY a.path
                LIMIT $limit
                """,
                parameters={"path": path_filter, "limit": limit},
            )
        else:
            r = conn.execute(
                """
                MATCH (d:Decision)-[:ANCHORS]->(a:Artifact)
                WITH a, MAX(d.timestamp) AS last_ts
                ORDER BY last_ts DESC
                LIMIT $limit
                RETURN a.path, a.kind
                """,
                parameters={"limit": limit},
            )

        rows = []
        while r.has_next():
            path, kind = r.get_next()
            rows.append(f"  {path}  ({kind})")

        text = "\n".join(rows) if rows else "no artifacts found"
        return [TextContent(type="text", text=text)]

    return [TextContent(type="text", text=f"preambulate: unknown tool {name!r}")]


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main() -> None:
    asyncio.run(_serve())


async def _serve() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
