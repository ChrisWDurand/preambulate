"""
Preambulate — community detection over the code graph.

Discovers clusters of related Artifacts using label propagation and writes
Cluster nodes with GOVERNS edges to their members.

Phases
------
A — file-level: community detection over the import graph
    (DERIVES_FROM edges between file/module Artifacts)
B — symbol-level: community detection over the call graph
    (DERIVES_FROM edges between symbol Artifacts — paths containing '::')

Usage:
    preambulate cluster                   # Phase A, default db
    preambulate cluster --phase B         # Phase B (requires cross-file edges)
    preambulate cluster --reset           # supersede existing clusters and recompute
    preambulate cluster --db ./memory.db  # explicit db path
"""

from __future__ import annotations

import argparse
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from preambulate import get_db_path
from preambulate.graph import GraphConnection, open_graph


ALGORITHM = "label_propagation"
MAX_ITER   = 50


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------
# Graph queries
# ------------------------------------------------------------

def _fetch_nodes(conn: GraphConnection, phase: str) -> list[str]:
    """Return artifact paths for the given phase.

    Phase A excludes package __init__.py files — they are gateways that
    connect modules to a package but do not represent conceptual centers.
    Including them collapses all importing modules into a single community
    (the gravity well problem), which constrains free exploration.
    """
    if phase == "A":
        rows = conn.execute(
            """
            MATCH (a:Artifact)
            WHERE a.kind IN ['module', 'file', 'document']
              AND NOT a.path CONTAINS '::'
              AND NOT a.path ENDS WITH '/__init__.py'
              AND NOT a.path = '__init__.py'
            RETURN a.path
            """
        )
    else:
        rows = conn.execute(
            """
            MATCH (a:Artifact)
            WHERE a.path CONTAINS '::'
            RETURN a.path
            """
        )
    return [r[0] for r in rows if r[0]]


def _fetch_edges(conn: GraphConnection, phase: str) -> list[tuple[str, str]]:
    """Return (src_path, tgt_path) DERIVES_FROM pairs for the given phase."""
    if phase == "A":
        rows = conn.execute(
            """
            MATCH (a:Artifact)-[:DERIVES_FROM]->(b:Artifact)
            WHERE NOT a.path CONTAINS '::'
              AND NOT b.path CONTAINS '::'
            RETURN a.path, b.path
            """
        )
    else:
        rows = conn.execute(
            """
            MATCH (a:Artifact)-[:DERIVES_FROM]->(b:Artifact)
            WHERE a.path CONTAINS '::'
              AND b.path CONTAINS '::'
            RETURN a.path, b.path
            """
        )
    return [(r[0], r[1]) for r in rows if r[0] and r[1]]


# ------------------------------------------------------------
# Label propagation
# ------------------------------------------------------------

def _label_propagation(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, str]:
    """
    Assign each node a cluster label via label propagation.
    Returns {node_path: label_string}.
    """
    if not nodes:
        return {}

    # Build undirected adjacency
    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        if a in set(nodes) and b in set(nodes):
            adj[a].add(b)
            adj[b].add(a)

    # Initialise: each node is its own label
    labels: dict[str, str] = {n: n for n in nodes}

    for _ in range(MAX_ITER):
        changed = False
        for node in nodes:
            neighbors = adj[node]
            if not neighbors:
                continue
            # Count neighbor labels
            counts: dict[str, int] = defaultdict(int)
            for nb in neighbors:
                counts[labels[nb]] += 1
            # Most common label (tie-break: lexicographic minimum)
            best = min(counts, key=lambda lbl: (-counts[lbl], lbl))
            if best != labels[node]:
                labels[node] = best
                changed = True
        if not changed:
            break

    return labels


# ------------------------------------------------------------
# Cluster naming
# ------------------------------------------------------------

def _name_cluster(members: list[str], phase: str) -> str:
    """
    Choose a human-readable label for a cluster.
    Use the most-connected member (highest degree within cluster).
    Falls back to shortest path basename.
    """
    member_set = set(members)
    # Pick the member whose basename is shortest and most recognisable
    if phase == "A":
        names = [Path(m).stem for m in members]
    else:
        names = [m.split("::")[-1] for m in members]

    # Prefer names that don't start with _ (private symbols)
    public = [n for n in names if not n.startswith("_")]
    pool = public if public else names
    return min(pool, key=len)


# ------------------------------------------------------------
# Graph writes
# ------------------------------------------------------------

def _existing_clusters(conn: GraphConnection, phase: str) -> list[tuple[str, str]]:
    """Return [(id, label)] of existing clusters for this phase."""
    rows = conn.execute(
        "MATCH (c:Cluster {phase: $phase}) RETURN c.id, c.label",
        parameters={"phase": phase},
    )
    return [(r[0], r[1]) for r in rows]


def _supersede_clusters(conn: GraphConnection, old_ids: list[str], new_id_: str) -> None:
    """Draw Cluster -[SUPERSEDES]-> old cluster edges."""
    ts = now()
    for old_id in old_ids:
        conn.execute(
            """
            MATCH (n:Cluster {id: $new_id}), (o:Cluster {id: $old_id})
            CREATE (n)-[:SUPERSEDES {
                weight: $w, traversal_cost: $tc,
                created_at: $ts, rationale: $r, reason: $reason
            }]->(o)
            """,
            parameters={
                "new_id": new_id_,
                "old_id": old_id,
                "w":      1.0,
                "tc":     0.0,
                "ts":     ts,
                "r":      "Cluster recomputed — old cluster archived.",
                "reason": "reset",
            },
        )


def _delete_cluster_governs(conn: GraphConnection, cluster_id: str) -> None:
    """Remove GOVERNS edges from a cluster node (before archiving it)."""
    conn.execute(
        """
        MATCH (c:Cluster {id: $id})-[r:GOVERNS]->(:Artifact)
        DELETE r
        """,
        parameters={"id": cluster_id},
    )


def _write_cluster(
    conn: GraphConnection,
    label: str,
    phase: str,
    members: list[str],
) -> str:
    """Create a Cluster node and its GOVERNS edges. Returns the cluster id."""
    cid = new_id()
    ts  = now()
    conn.execute(
        """
        CREATE (c:Cluster {
            id:               $id,
            label:            $label,
            algorithm:        $alg,
            phase:            $phase,
            created_at:       $ts,
            membership_count: $count
        })
        """,
        parameters={
            "id":    cid,
            "label": label,
            "alg":   ALGORITHM,
            "phase": phase,
            "ts":    ts,
            "count": len(members),
        },
    )

    for path in members:
        rows = conn.execute(
            "MATCH (a:Artifact {path: $path}) RETURN a.id LIMIT 1",
            parameters={"path": path},
        )
        if not rows:
            continue
        conn.execute(
            """
            MATCH (c:Cluster {id: $cid}), (a:Artifact {path: $path})
            CREATE (c)-[:GOVERNS {
                weight: $w, traversal_cost: $tc,
                created_at: $ts, rationale: $r
            }]->(a)
            """,
            parameters={
                "cid":  cid,
                "path": path,
                "w":    1.0,
                "tc":   0.0,
                "ts":   ts,
                "r":    f"Cluster '{label}' (phase {phase}) contains this artifact.",
            },
        )

    return cid


# ------------------------------------------------------------
# Main cluster routine
# ------------------------------------------------------------

def cluster(conn: GraphConnection, phase: str = "A", reset: bool = False) -> int:
    """
    Run community detection and write Cluster nodes.
    Returns the number of clusters written.
    """
    nodes = _fetch_nodes(conn, phase)
    if not nodes:
        print(f"  no artifacts found for phase {phase} — nothing to cluster")
        return 0

    edges = _fetch_edges(conn, phase)
    print(f"  phase {phase}: {len(nodes)} nodes, {len(edges)} edges")

    labels = _label_propagation(nodes, edges)

    # Group nodes by label
    groups: dict[str, list[str]] = defaultdict(list)
    for node, lbl in labels.items():
        groups[lbl].append(node)

    print(f"  clusters discovered: {len(groups)}")

    # Handle reset — supersede existing clusters
    existing = _existing_clusters(conn, phase)
    old_ids  = [eid for eid, _ in existing]

    if reset and old_ids:
        for eid in old_ids:
            _delete_cluster_governs(conn, eid)

    # Write new clusters
    new_cluster_ids = []
    for members in groups.values():
        label = _name_cluster(members, phase)
        cid   = _write_cluster(conn, label, phase, members)
        new_cluster_ids.append(cid)
        print(f"    cluster '{label}' — {len(members)} member(s)")

    # Draw supersedes edges after all new clusters exist
    if reset and old_ids:
        for cid in new_cluster_ids:
            _supersede_clusters(conn, old_ids, cid)

    return len(groups)


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Discover artifact clusters via community detection.")
    parser.add_argument("--phase", choices=["A", "B"], default="A",
                        help="A = file-level (default), B = symbol-level")
    parser.add_argument("--db",    type=Path, default=get_db_path())
    parser.add_argument("--reset", action="store_true",
                        help="Supersede existing clusters and recompute")
    args = parser.parse_args()

    if not args.db.exists():
        print("preambulate cluster: no database found — run 'preambulate init' first")
        return

    conn  = open_graph(args.db)
    count = cluster(conn, phase=args.phase, reset=args.reset)
    print(f"preambulate cluster: phase {args.phase} complete — {count} cluster(s) written")


if __name__ == "__main__":
    main()
