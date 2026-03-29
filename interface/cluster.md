# Cluster Node Type — Interface Specification

_Owner: preambulate/cluster.py (new), preambulate/schema.cypher_
_Depends on: symbol extraction (Task 2, complete); cross-file resolution (Task 4, spec)_
_Implements: Tasks 3 (Phase A) and 5 (Phase B)_
_Version: 0.1.0_
_Status: DRAFT — pending review before implementation_

---

## Purpose

A cluster is a group of artifacts that form a cohesive region of the code graph.
Clusters are discovered, not declared — they emerge from the structure of
`DERIVES_FROM` and `GOVERNS` edges already in the graph.

A cluster connects the code structure layer (what files and symbols exist) to the
decision layer (why they were built). Once a file belongs to a cluster, the path
`file → cluster → decisions that shaped it` becomes traversable. This is the
GitNexus-compatible property: function call chains traceable upward through the
decisions that shaped them.

---

## Node Type: Cluster

**Added to schema.cypher:**

```sql
CREATE NODE TABLE Cluster (
    id              STRING,
    label           STRING,
    algorithm       STRING,
    phase           STRING,
    created_at      TIMESTAMP,
    membership_count INT64,
    PRIMARY KEY (id)
);
```

| Property | Type | Description |
|---|---|---|
| `id` | UUID string | Stable identifier |
| `label` | string | Human-readable name — generated or user-assigned |
| `algorithm` | string | Community detection algorithm used. e.g. `"louvain"`, `"label_propagation"` |
| `phase` | enum string | `"A"` (file-level) or `"B"` (symbol-level) |
| `created_at` | TIMESTAMP | When this cluster was computed |
| `membership_count` | INT64 | Number of member artifacts |

---

## Relationships

### Cluster -[GOVERNS]-> Artifact

Membership edge. The cluster governs the interpretation of its member artifacts.

**Schema change required:** add `FROM Cluster TO Artifact` to the GOVERNS REL
TABLE GROUP in both `schema.cypher` files.

```sql
CREATE REL TABLE GROUP GOVERNS (
    ...existing pairs...
    FROM Cluster  TO Artifact,   -- new
    ...
```

No new edge type. GOVERNS is the correct semantic: a cluster defines the
interpretive frame for navigating its members.

### Cluster -[SUPERSEDES]-> Cluster

When clustering is re-run, the new cluster supersedes the old one for the same
region. Old clusters are never deleted — they are archived via SUPERSEDES.

**Schema change required:** add `FROM Cluster TO Cluster` to the SUPERSEDES REL
TABLE GROUP.

---

## Phase A — File-Level Community Detection

**Input:** All `Artifact` nodes where `kind` is `module` or `file`, connected by
`DERIVES_FROM` edges (import graph).

**Algorithm:**

1. Query all file-level DERIVES_FROM edges (exclude symbol `::` paths).
2. Build an undirected adjacency list from those edges (imports are directional
   but community structure is symmetric for clustering purposes).
3. Run label propagation over the adjacency list.
   - Label propagation is chosen over Louvain: no external dependency required,
     implementable in pure Python, sufficient for the scale of a single project.
   - Each node starts with a unique label. On each iteration, each node adopts the
     most common label among its neighbors (ties broken by lowest label id).
   - Terminate when no node changes label (convergence) or at max 50 iterations.
4. Each resulting label group becomes a Cluster node.
5. For each member artifact, write: `Cluster -[GOVERNS]-> Artifact`

**Labeling:**

Cluster labels are generated from the most central node in the group (highest
degree within the cluster). E.g. if `preambulate/graph.py` has the most edges
within its cluster, the cluster is labeled `"graph"`.

User may rename clusters after generation — label is mutable.

**When to run:**

- `preambulate cluster --phase A` (explicit)
- Automatically after `infer_all()` when `--cluster` flag is set

---

## Phase B — Symbol-Level Community Detection

**Input:** All symbol `Artifact` nodes (paths containing `::`), connected by
`DERIVES_FROM` edges from Phases 3 and 4 of inference.

**Algorithm:** Identical to Phase A (label propagation), run over the symbol
call graph instead of the file import graph.

**Dependency:** Phase B requires Task 4 (cross-file call resolution) to be
complete. Running Phase B without cross-file edges produces clusters that are
identical to file membership (symbols in the same file are only connected to
each other). This is not wrong, but is not more informative than Phase A.

Phase B is a scheduled follow-on to Task 4, not a parallel track.

---

## CLI Contract

### `preambulate cluster`

```
preambulate cluster [--phase A|B] [--db ./memory.db] [--reset]
```

| Flag | Default | Description |
|---|---|---|
| `--phase` | `A` | Which clustering phase to run |
| `--db` | from `get_db_path()` | Database path |
| `--reset` | false | Drop existing clusters for this phase before recomputing |

**Output (stdout):**

```
clustering: phase A — file-level import graph
  nodes: 12
  edges: 23
  clusters discovered: 3
    cluster "graph"       — 3 members
    cluster "sync"        — 4 members
    cluster "cli"         — 5 members
  GOVERNS edges written: 12
done.
```

**Idempotency:**

Without `--reset`: re-run is a no-op. Existing clusters and GOVERNS edges are
not duplicated. Any file not yet in a cluster is added to an existing cluster
or forms a singleton cluster.

With `--reset`: existing Phase A (or Phase B) clusters are marked as superseded
via `Cluster -[SUPERSEDES]-> Cluster` (old superseded by new), then new clusters
are computed from scratch.

---

## Traversal Enabled by Clusters

After Phase A, the following traversal is valid in the graph:

```
(Artifact: preambulate/sync.py::_push)
  <-[GOVERNS]- (Artifact: preambulate/sync.py)
  <-[GOVERNS]- (Cluster: sync)
  -[GOVERNS]-> (Artifact: preambulate/graph.py)  -- via cluster membership
  <-[GOVERNS]- (Decision: "Add GraphConnection abstraction")
  <-[ANCHORS]- ...
```

This connects a specific function through its cluster, across to related files,
and up through the decisions that shaped the design. This is the intended
traversal path from code structure to decision history.

---

## Schema Migration

The Cluster node type and new GOVERNS/SUPERSEDES pairs are additive. Existing
databases can be migrated by:

1. `preambulate export dump`
2. `preambulate init --reset`
3. `preambulate export restore --dump graph_export.json`
4. `preambulate cluster --phase A`

No existing nodes or edges are modified by the schema change.

---

## Files Affected

| File | Change |
|---|---|
| `preambulate/schema.cypher` | Add Cluster node table; add Cluster pairs to GOVERNS and SUPERSEDES |
| `schema.cypher` (root spec) | Same changes — keep in sync |
| `schema.spec.md` | Add Cluster to Node Types section; add Cluster to GOVERNS and SUPERSEDES entries |
| `preambulate/cluster.py` | New file — implements label propagation, node/edge writes, CLI entry |
| `preambulate/cli.py` | Add `cluster` subcommand dispatch |
| `pyproject.toml` | Add `preambulate cluster = preambulate.cluster:main` entry point |
| `preambulate/export.py` | Add Cluster to NODE_TABLES and EDGE_SPECS |

---

## Acceptance Criteria

**Phase A:**
- [ ] `preambulate cluster` on the preambulate codebase produces ≥ 2 clusters
- [ ] Every module-kind Artifact is a member of exactly one cluster
- [ ] Re-running without `--reset` produces no new edges
- [ ] `preambulate cluster --reset` marks old clusters superseded before creating new ones
- [ ] `preambulate export dump` includes Cluster nodes and their GOVERNS edges

**Phase B (after Task 4):**
- [ ] Symbol-level clusters are more granular than file-level clusters
- [ ] `_push`, `_pull`, `_common_headers` in sync.py belong to the same symbol cluster
- [ ] Phase B clusters do not affect Phase A clusters (separate `phase` property)
