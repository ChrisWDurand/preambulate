"""
Preambulate — graph export and restore.

Dumps all nodes and edges to a JSON file, and restores from that file
into a freshly-initialised database.  Intended for schema migrations
where init.py --reset would otherwise destroy live data.

Usage:
    python export.py dump                              # dump to graph_export.json
    python export.py dump --out backup.json            # explicit output path
    python export.py restore --dump graph_export.json  # restore into current DB

Restore sequence for a schema migration:
    python export.py dump
    python init.py --reset
    python export.py restore --dump graph_export.json

The restore step clears the seed geometry that init.py inserts (new UUIDs)
before re-importing the original nodes so that existing edges remain valid.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import kuzu

from preambulate import get_db_path


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

DEFAULT_DB_PATH = get_db_path()
DEFAULT_DUMP     = Path("graph_export.json")

NODE_TYPES = ["Seed", "Concept", "Artifact", "Context", "Observation", "Decision"]

NODE_PROPS = {
    "Seed":        ["id", "phrase", "created_at"],
    "Concept":     ["id", "label", "definition", "depth"],
    "Artifact":    ["id", "label", "path", "kind"],
    "Context":     ["id", "label", "active"],
    "Observation": ["id", "label", "source", "confidence"],
    "Decision":    ["id", "label", "rationale", "timestamp", "session_id",
                    "author", "machine_id"],
}

# (rel_type, from_type, to_type, extra_props)
EDGE_SPECS: list[tuple[str, str, str, list[str]]] = [
    # GOVERNS
    ("GOVERNS", "Seed",    "Concept",  []),
    ("GOVERNS", "Concept", "Concept",  []),
    ("GOVERNS", "Concept", "Artifact", []),
    ("GOVERNS", "Context", "Concept",  []),
    ("GOVERNS", "Context", "Artifact", []),
    # DERIVES_FROM
    ("DERIVES_FROM", "Concept",     "Concept",  []),
    ("DERIVES_FROM", "Concept",     "Artifact", []),
    ("DERIVES_FROM", "Artifact",    "Artifact", []),
    ("DERIVES_FROM", "Artifact",    "Decision", []),
    ("DERIVES_FROM", "Observation", "Concept",  []),
    ("DERIVES_FROM", "Observation", "Decision", []),
    # CONSTRAINS
    ("CONSTRAINS", "Concept",  "Concept",  []),
    ("CONSTRAINS", "Concept",  "Artifact", []),
    ("CONSTRAINS", "Concept",  "Context",  []),
    ("CONSTRAINS", "Context",  "Concept",  []),
    ("CONSTRAINS", "Context",  "Artifact", []),
    ("CONSTRAINS", "Context",  "Context",  []),
    ("CONSTRAINS", "Decision", "Concept",  []),
    ("CONSTRAINS", "Decision", "Artifact", []),
    ("CONSTRAINS", "Decision", "Context",  []),
    # DEFINES
    ("DEFINES", "Concept", "Concept", []),
    ("DEFINES", "Context", "Concept", []),
    # INSTANTIATES
    ("INSTANTIATES", "Artifact", "Concept", []),
    ("INSTANTIATES", "Decision", "Concept", []),
    # ANCHORS
    ("ANCHORS", "Decision", "Concept",     ["anchor_type"]),
    ("ANCHORS", "Decision", "Artifact",    ["anchor_type"]),
    ("ANCHORS", "Decision", "Context",     ["anchor_type"]),
    ("ANCHORS", "Decision", "Observation", ["anchor_type"]),
    # SUPERSEDES
    ("SUPERSEDES", "Concept",     "Concept",     ["reason"]),
    ("SUPERSEDES", "Artifact",    "Artifact",    ["reason"]),
    ("SUPERSEDES", "Context",     "Context",     ["reason"]),
    ("SUPERSEDES", "Observation", "Observation", ["reason"]),
    ("SUPERSEDES", "Decision",    "Decision",    ["reason"]),
    # RESONATES_WITH
    ("RESONATES_WITH", "Concept",     "Concept",     ["resonance_basis"]),
    ("RESONATES_WITH", "Concept",     "Artifact",    ["resonance_basis"]),
    ("RESONATES_WITH", "Concept",     "Context",     ["resonance_basis"]),
    ("RESONATES_WITH", "Concept",     "Observation", ["resonance_basis"]),
    ("RESONATES_WITH", "Concept",     "Decision",    ["resonance_basis"]),
    ("RESONATES_WITH", "Artifact",    "Artifact",    ["resonance_basis"]),
    ("RESONATES_WITH", "Artifact",    "Context",     ["resonance_basis"]),
    ("RESONATES_WITH", "Artifact",    "Observation", ["resonance_basis"]),
    ("RESONATES_WITH", "Artifact",    "Decision",    ["resonance_basis"]),
    ("RESONATES_WITH", "Context",     "Context",     ["resonance_basis"]),
    ("RESONATES_WITH", "Context",     "Observation", ["resonance_basis"]),
    ("RESONATES_WITH", "Context",     "Decision",    ["resonance_basis"]),
    ("RESONATES_WITH", "Observation", "Observation", ["resonance_basis"]),
    ("RESONATES_WITH", "Observation", "Decision",    ["resonance_basis"]),
    ("RESONATES_WITH", "Decision",    "Decision",    ["resonance_basis"]),
    # OPPOSES
    ("OPPOSES", "Concept",     "Concept",     ["tension_description"]),
    ("OPPOSES", "Concept",     "Artifact",    ["tension_description"]),
    ("OPPOSES", "Artifact",    "Artifact",    ["tension_description"]),
    ("OPPOSES", "Decision",    "Decision",    ["tension_description"]),
]

BASE_EDGE_PROPS = ["weight", "traversal_cost", "created_at", "rationale"]


# ------------------------------------------------------------
# Serialisation helpers
# ------------------------------------------------------------

def _serial(v: object) -> object:
    """Make a value JSON-serialisable."""
    if isinstance(v, datetime):
        return v.isoformat()
    return v


# Properties that must be converted back to datetime on restore.
_TIMESTAMP_PROPS = {"created_at", "timestamp"}


def _deserial(key: str, v: object) -> object:
    """Reverse _serial: parse ISO timestamp strings back to datetime."""
    if key in _TIMESTAMP_PROPS and isinstance(v, str):
        return datetime.fromisoformat(v)
    return v


# ------------------------------------------------------------
# Dump
# ------------------------------------------------------------

def dump(conn: kuzu.Connection, out_path: Path) -> None:
    data: dict = {
        "version":     "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "nodes":       {},
        "edges":       [],
    }

    # Nodes
    total_nodes = 0
    for ntype in NODE_TYPES:
        props = NODE_PROPS[ntype]
        # Some properties may not exist in older schemas — probe and skip missing.
        available = []
        for p in props:
            try:
                conn.execute(f"MATCH (n:{ntype}) RETURN n.{p} LIMIT 0")
                available.append(p)
            except RuntimeError:
                pass  # property not in this schema version
        prop_list = ", ".join(f"n.{p} AS {p}" for p in available)
        r = conn.execute(f"MATCH (n:{ntype}) RETURN {prop_list}")
        rows = []
        while r.has_next():
            row = r.get_next()
            record = {p: _serial(v) for p, v in zip(available, row)}
            # Fill missing properties with None so restore targets the full schema
            for p in props:
                if p not in record:
                    record[p] = None
            rows.append(record)
        data["nodes"][ntype] = rows
        total_nodes += len(rows)
        if rows:
            print(f"  {ntype}: {len(rows)} node(s)")

    # Edges
    total_edges = 0
    for rel, from_type, to_type, extra in EDGE_SPECS:
        all_props = BASE_EDGE_PROPS + extra
        prop_list = ", ".join(f"r.{p} AS {p}" for p in all_props)
        r = conn.execute(
            f"""
            MATCH (a:{from_type})-[r:{rel}]->(b:{to_type})
            RETURN a.id AS from_id, b.id AS to_id, {prop_list}
            """
        )
        while r.has_next():
            row   = r.get_next()
            entry = {
                "rel":       rel,
                "from_type": from_type,
                "to_type":   to_type,
                "from_id":   row[0],
                "to_id":     row[1],
            }
            for i, p in enumerate(all_props):
                entry[p] = _serial(row[2 + i])
            data["edges"].append(entry)
            total_edges += 1

    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"exported {total_nodes} nodes, {total_edges} edges → {out_path}")


# ------------------------------------------------------------
# Restore
# ------------------------------------------------------------

def _clear_init_geometry(conn: kuzu.Connection) -> None:
    """
    Remove the seed geometry that init.py inserted (new UUIDs).
    At this point the DB contains only those nodes — safe to delete all.
    Edges must be deleted before nodes in Kuzu.
    """
    conn.execute("MATCH (s:Seed)-[r:GOVERNS]->(c:Concept) DELETE r")
    conn.execute("MATCH (a:Concept)-[r:GOVERNS]->(b:Concept) DELETE r")
    conn.execute("MATCH (a:Concept)-[r:CONSTRAINS]->(b:Concept) DELETE r")
    conn.execute("MATCH (a:Concept)-[r:DERIVES_FROM]->(b:Concept) DELETE r")
    conn.execute("MATCH (s:Seed) DELETE s")
    conn.execute("MATCH (c:Concept) DELETE c")
    print("  cleared init.py seed geometry")


def _restore_nodes(conn: kuzu.Connection, data: dict) -> int:
    total = 0
    for ntype, rows in data.items():
        if not rows:
            continue
        props = NODE_PROPS.get(ntype, list(rows[0].keys()))
        for row in rows:
            placeholders = ", ".join(f"{p}: ${p}" for p in props)
            params = {p: _deserial(p, row.get(p)) for p in props}
            conn.execute(
                f"CREATE (n:{ntype} {{{placeholders}}})",
                parameters=params,
            )
            total += 1
        print(f"  {ntype}: restored {len(rows)} node(s)")
    return total


def _restore_edges(conn: kuzu.Connection, edges: list) -> int:
    total = 0
    for e in edges:
        rel        = e["rel"]
        from_type  = e["from_type"]
        to_type    = e["to_type"]
        all_props  = BASE_EDGE_PROPS + [
            k for k in e
            if k not in ("rel", "from_type", "to_type", "from_id", "to_id")
            and k not in BASE_EDGE_PROPS
        ]
        prop_set   = ", ".join(
            f"{p}: ${p}" for p in all_props if e.get(p) is not None
        )
        params = {"from_id": e["from_id"], "to_id": e["to_id"]}
        for p in all_props:
            params[p] = _deserial(p, e.get(p))

        conn.execute(
            f"""
            MATCH (a:{from_type} {{id: $from_id}}), (b:{to_type} {{id: $to_id}})
            CREATE (a)-[:{rel} {{{prop_set}}}]->(b)
            """,
            parameters=params,
        )
        total += 1
    return total


def restore(conn: kuzu.Connection, dump_path: Path) -> None:
    data  = json.loads(dump_path.read_text(encoding="utf-8"))
    print(f"restoring from {dump_path}  (exported {data['exported_at']})")

    _clear_init_geometry(conn)

    node_count = _restore_nodes(conn, data["nodes"])
    edge_count = _restore_edges(conn, data["edges"])

    print(f"restored {node_count} nodes, {edge_count} edges")


# ------------------------------------------------------------
# Verification
# ------------------------------------------------------------

def verify(conn: kuzu.Connection) -> None:
    """Spot-check that the restore looks correct."""
    print("\nverification:")

    # Decision nodes have author and machine_id columns
    r = conn.execute(
        "MATCH (d:Decision) RETURN d.id, d.author, d.machine_id LIMIT 3"
    )
    found = False
    while r.has_next():
        found = True
        did, author, machine_id = r.get_next()
        print(f"  Decision {did[:8]}  author={author!r}  machine_id={machine_id!r}")
    if not found:
        print("  (no Decision nodes)")

    # Node counts
    for ntype in NODE_TYPES:
        r = conn.execute(f"MATCH (n:{ntype}) RETURN COUNT(*)")
        count = r.get_next()[0] if r.has_next() else 0
        if count:
            print(f"  {ntype}: {count}")

    # Edge count
    total_edges = 0
    for rel, from_type, to_type, _ in EDGE_SPECS:
        r = conn.execute(
            f"MATCH (:{from_type})-[r:{rel}]->(:{to_type}) RETURN COUNT(*)"
        )
        n = r.get_next()[0] if r.has_next() else 0
        total_edges += n
    print(f"  edges total: {total_edges}")


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export and restore the preambulate graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dump = sub.add_parser("dump", help="Dump graph to JSON.")
    p_dump.add_argument("--db",  type=Path, default=DEFAULT_DB_PATH)
    p_dump.add_argument("--out", type=Path, default=DEFAULT_DUMP)

    p_restore = sub.add_parser("restore", help="Restore graph from JSON dump.")
    p_restore.add_argument("--db",   type=Path, default=DEFAULT_DB_PATH)
    p_restore.add_argument("--dump", type=Path, default=DEFAULT_DUMP)

    args = parser.parse_args()

    if not args.db.exists():
        print(f"no database at {args.db}")
        return

    db   = kuzu.Database(str(args.db))
    conn = kuzu.Connection(db)

    if args.cmd == "dump":
        dump(conn, args.out)
    elif args.cmd == "restore":
        restore(conn, args.dump)
        verify(conn)


if __name__ == "__main__":
    main()
