# Graph Memory Schema Specification

**Seed geometry:** `geometry governs exploration`
**Storage target:** Kuzu (embedded, local-first, Cypher queries, Python bindings)
**Version:** 0.1.0

---

## Design Principles

1. **Geometry is load-bearing.** The shape of the graph determines what is discoverable. Structure is not decorative.
2. **Edges carry memory.** Every relationship has a `rationale` — the reason it was drawn. Without it, the graph is topology without history.
3. **Position is semantics.** A node's `depth` from the seed, its cluster membership, and its centrality are first-class facts, not computed afterthoughts.
4. **Conversations have temporal addresses.** A `Decision` node anchors a moment in a chat to a moment in the graph. Without it, conversation memory is stateless.
5. **Local-first.** The graph travels with the project. No server, no external dependencies.

---

## Node Types

### Concept
An atomic idea, term, or principle. The vocabulary of the graph.

| Property | Type | Required | Description |
|---|---|---|---|
| `id` | UUID | yes | Stable identifier |
| `label` | string | yes | Human-readable name |
| `definition` | string | no | What this concept means in this project's context |
| `depth` | int | yes | Distance from the nearest Seed node. 0 = seed-adjacent |

> `depth` is set at write time and updated on structural change. It is not lazily computed.

---

### Artifact
A file, document, code module, or any created thing that exists outside the graph but is referenced within it.

| Property | Type | Required | Description |
|---|---|---|---|
| `id` | UUID | yes | Stable identifier |
| `label` | string | yes | Human-readable name |
| `path` | string | no | Relative path within the project |
| `kind` | enum | yes | One of: `file`, `module`, `document`, `output`, `external` |

---

### Context
A situational frame — a project phase, an active constraint, a goal, or a lens through which part of the graph is interpreted.

| Property | Type | Required | Description |
|---|---|---|---|
| `id` | UUID | yes | Stable identifier |
| `label` | string | yes | Human-readable name |
| `active` | bool | yes | Whether this context is currently operative |

> Contexts may be deactivated but should not be deleted. Deactivated contexts preserve the history of what was true at a given time.

---

### Observation
Something noticed or learned — raw or processed. The epistemically humble node type. Observations become Concepts when they harden into principles.

| Property | Type | Required | Description |
|---|---|---|---|
| `id` | UUID | yes | Stable identifier |
| `label` | string | yes | Human-readable name |
| `source` | string | no | Where this came from (session, file, person) |
| `confidence` | float | yes | 0.0–1.0. How settled this is |

---

### Decision
A specific moment in a conversation where a choice was made and anchored to one or more graph nodes. The temporal address of conversation memory.

| Property | Type | Required | Description |
|---|---|---|---|
| `id` | UUID | yes | Stable identifier |
| `label` | string | yes | Short description of the decision |
| `rationale` | string | yes | Why this choice was made |
| `timestamp` | datetime (ISO 8601) | yes | When the decision occurred |
| `session_id` | string | yes | Identifier of the conversation session |
| `author` | string | no | Identity of the person or agent who made this decision. Null in v1 (single-user). Populated in v2 for attribution and conflict resolution. |
| `machine_id` | string | no | Stable identifier of the machine where the decision was made. Null in v1. Populated in v2 to track which client last wrote a region. |
| `decision_type` | enum | no | Who initiated this decision. Values: `user` (explicit user choice), `claude_inferred` (hook-captured artifact write), `claude_autonomous` (session lifecycle hook), `blocked` (action was blocked). |
| `rationale_source` | enum | no | How the rationale was produced. Values: `user_stated` (user provided it), `claude_inferred` (generated from context), `system_blocked` (rationale is a block reason). |

> A Decision node is the join point between a conversation thread and the graph. It connects *when* to *what*. Without `session_id` + `timestamp`, the memory has no temporal address and cannot be replayed or audited.
>
> `author` and `machine_id` are ownership fields required for v2 conflict resolution. They are optional in v1 but present in the schema from the start so no migration is needed at the v1→v2 boundary.

---

### Seed
The origin node. Anchors the geometry. Every graph has exactly one Seed.

| Property | Type | Required | Description |
|---|---|---|---|
| `id` | UUID | yes | Stable identifier |
| `phrase` | string | yes | The founding phrase |
| `created_at` | datetime (ISO 8601) | yes | When the graph was initialized |

> The Seed is immutable after creation. It is the fixed point from which all `depth` values are measured.

---

## Edge Types

All edges carry the following **base properties**:

| Property | Type | Required | Description |
|---|---|---|---|
| `weight` | float | yes | Relationship strength, 0.0–1.0 |
| `traversal_cost` | float | yes | Cost to cross during exploration, 0.0–1.0. Default: `1.0 - weight` |
| `created_at` | datetime (ISO 8601) | yes | When this relationship was drawn |
| `rationale` | string | yes | Why this edge exists. This is the memory. |

---

### DEFINES
One concept draws the boundary of another. Establishes what something is by saying what it is not or what it contains.

- **Direction:** directed (source → target)
- **Valid source types:** `Concept`, `Context`
- **Valid target types:** `Concept`

---

### DERIVES_FROM
The target is the origin of the source. Source exists because target existed first.

- **Direction:** directed (source → target)
- **Valid source types:** `Concept`, `Artifact`, `Observation`
- **Valid target types:** `Concept`, `Artifact`, `Decision`

---

### CONSTRAINS
The source limits the solution space of the target. Not blocking — shaping.

- **Direction:** directed (source → target)
- **Valid source types:** `Concept`, `Context`, `Decision`
- **Valid target types:** `Concept`, `Artifact`, `Context`

---

### GOVERNS
The structure of the source shapes how the target is traversed or interpreted. A meta-relationship — it acts on the graph's own geometry.

- **Direction:** directed (source → target)
- **Valid source types:** `Concept`, `Seed`, `Context`
- **Valid target types:** `Concept`, `Artifact`, `Context`

> This edge type is named by the seed phrase. The graph uses it to describe its own structure.

---

### RESONATES_WITH
Structural similarity. The source and target occupy similar positions in the graph without being identical. The graph's equivalent of proximity.

- **Direction:** undirected
- **Valid types:** any node type, same or mixed
- **Additional property:** `resonance_basis: string` — a brief note on what makes them similar

---

### OPPOSES
Productive tension. Source and target are in conflict that neither resolves by consuming the other. Both nodes remain valid.

- **Direction:** undirected
- **Valid types:** any node type, same or mixed
- **Additional property:** `tension_description: string` — what the conflict is

---

### INSTANTIATES
An artifact is a concrete realization of a concept.

- **Direction:** directed (source → target)
- **Valid source types:** `Artifact`, `Decision`
- **Valid target types:** `Concept`

---

### SUPERSEDES
Temporal replacement. The source replaces the target. The old relationship is preserved and flagged, not deleted.

- **Direction:** directed (source → target)
- **Valid source types:** any node type
- **Valid target types:** same type as source
- **Additional property:** `reason: string` — why the replacement occurred

> Superseded nodes are never deleted. They are archived. The graph's history is part of its geometry.

---

### ANCHORS
Connects a Decision node to the graph node it was made about. This is the conversation-to-graph join.

- **Direction:** directed (source → target)
- **Valid source types:** `Decision`
- **Valid target types:** `Concept`, `Artifact`, `Context`, `Observation`
- **Additional property:** `anchor_type: enum` — one of: `created`, `modified`, `discussed`, `rejected`

---

## Seed Instantiation

The founding phrase `geometry governs exploration` self-instantiates:

```
(Seed {phrase: "geometry governs exploration"})
  -[GOVERNS]-> (Concept {label: "geometry",    depth: 0})
  -[GOVERNS]-> (Concept {label: "governs",     depth: 0})
  -[GOVERNS]-> (Concept {label: "exploration", depth: 0})

(Concept: geometry)    -[GOVERNS]->  (Concept: exploration)
(Concept: governs)     -[DEFINES]->  (edge type: GOVERNS)    // reflexive — governs names itself
(Concept: geometry)    -[CONSTRAINS]-> (Concept: exploration) // shape limits what can be found
(Concept: exploration) -[DERIVES_FROM]-> (Concept: geometry)  // discovery is downstream of structure
```

The seed phrase folds back on itself: `governs` names the edge type the seed uses to introduce itself. This reflexivity is not accidental — the founding geometry is self-describing.

---

## Traversal Modes

The schema supports three named traversal patterns. These are not queries — they are modes of movement through the graph.

### 1. Radial
Expand outward from a focal node, respecting `depth` and `traversal_cost`. Used for: "what surrounds this concept?"

- Walk edges ordered by ascending `traversal_cost`
- Stop at configurable `max_depth` or `max_cost` budget
- Returns a subgraph centered on the focal node

### 2. Resonance Walk
Follow `RESONATES_WITH` edges to find structural siblings. Used for: "what is like this?"

- Enter at any node
- Traverse only `RESONATES_WITH` edges
- Rank results by `weight`
- Useful for finding analogues when you don't know the exact label

### 3. Constraint-First
Enter via `CONSTRAINS` edges before exploring a domain. Used for: "what shapes this space before I enter it?"

- Start at a `Context` or `Concept`
- Walk `CONSTRAINS` edges inward to the target domain
- Surface all constraints before traversing the domain itself
- Prevents exploring a space without understanding what limits it

---

## Kuzu Implementation Notes

These notes are non-normative. The spec above is storage-agnostic; these are translation hints for the Kuzu target.

- Kuzu uses a **node table / relationship table** model. Each node type and edge type maps to a separate table.
- Node tables require a `PRIMARY KEY`. Use `id UUID` as the primary key on all node tables.
- Kuzu supports `SERIAL` and `UUID` types natively as of recent versions. Prefer `STRING` for UUIDs if `UUID` type is unavailable in the target version.
- `datetime` maps to Kuzu's `TIMESTAMP` type.
- `enum` fields should be stored as `STRING` with application-layer validation until Kuzu's enum support stabilizes.
- `float` maps to `DOUBLE`.
- `bool` maps to `BOOLEAN`.
- **`NOT NULL` is not supported in Kuzu DDL.** Omit it entirely; enforce required fields at the application layer.
- **Relationships with multiple FROM-TO pairs must use `CREATE REL TABLE GROUP`**, not `CREATE REL TABLE`. Single-pair relationships may use either form, but `GROUP` is safe for all cases.
- **DDL execution is one statement per call.** When driving DDL from Python, split on `;` and execute each statement individually. Strip comment lines *before* splitting — comments containing semicolons will otherwise be treated as statement delimiters.
- Undirected edges (`RESONATES_WITH`, `OPPOSES`) must be modeled as directed in Kuzu (all relationships are directed). Treat them as bidirectional by convention: always insert both directions, or query with `MATCH (a)-[r:RESONATES_WITH]-(b)` using the undirected Cypher syntax.
- The `ANCHORS` relationship is the primary join between the session layer and the graph layer. Index on `Decision.session_id` for efficient session replay.

---

## Invariants

These must hold at all times:

1. Exactly one `Seed` node exists per graph.
2. Every non-Seed node has at least one inbound edge.
3. `depth` on `Concept` nodes must equal the shortest path length to any `Seed`-adjacent node.
4. `Decision.session_id` must be non-null and non-empty.
5. Superseded nodes are never deleted — only marked via a `SUPERSEDES` edge.
6. Every edge has a non-empty `rationale`. An edge without a rationale is not a memory — it is noise.
