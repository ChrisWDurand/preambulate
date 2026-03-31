# preambulate

Graph-based project memory for Claude Code. Every session is captured as a Decision node in a local [Kuzu](https://kuzudb.com/) graph database, anchored to the files and concepts touched during that session.

Over time, preambulate builds a traversable record of *why* your code is the way it is — not just what changed, but the reasoning behind it.

---

## What it does

- **Captures sessions** — each Claude Code session writes a Decision node recording what was done and why
- **Tracks artifacts** — file edits are recorded as Artifact nodes linked to the decisions that created them
- **Infers structure** — import relationships and function call chains are extracted from Python source and written as graph edges
- **Clusters code** — community detection groups related files and symbols into Cluster nodes
- **Briefs you** — at session start, prints recently active files and the decisions behind them

---

## Install

```bash
pipx install preambulate
preambulate install
```

`preambulate install` writes `SessionStart`, `PostToolUse`, and `Stop` hooks to `~/.claude/settings.json`. Safe to run more than once.

After that, preambulate runs automatically in every Claude Code session. No per-project setup required.

On first use, Claude Code will prompt you to approve the hook commands. Approve them — this is a one-time step.

To initialise a database manually (optional — happens automatically on first session):

```bash
preambulate init
```

---

## Commands

| Command | What it does |
|---|---|
| `preambulate init` | Initialise a new graph database (`memory.db`) |
| `preambulate install` | Write hooks to `~/.claude/settings.json` |
| `preambulate capture` | Record a session-start Decision node (run by hook) |
| `preambulate artifact` | Record a file edit as Artifact + Decision (run by hook) |
| `preambulate infer` | Infer DERIVES_FROM edges from Python imports and calls |
| `preambulate cluster` | Discover artifact clusters via community detection |
| `preambulate decision` | Record a session-end Decision and write semantic edges |
| `preambulate briefing` | Print the memory briefing |
| `preambulate export` | Dump or restore the full graph to/from JSON |
| `preambulate mcp` | Start the MCP server (stdio transport) |

---

## How it works

### Hooks

Three hooks fire automatically once `preambulate install` has run:

| Hook | Trigger | What it does |
|---|---|---|
| `SessionStart` | Session opens | Creates a session Decision node; prints briefing |
| `PostToolUse` (Write/Edit) | File saved | Records the file as an Artifact; infers import edges |
| `Stop` | Each response | Pushes incremental changes (no-op if sync not configured) |

### Graph

The graph is a local [Kuzu](https://kuzudb.com/) database stored in `memory.db/` at the project root. Node types:

| Type | Represents |
|---|---|
| `Seed` | The founding anchor — one per graph |
| `Concept` | An idea, term, or principle |
| `Artifact` | A file, symbol, or document |
| `Cluster` | A community of related artifacts |
| `Decision` | A recorded choice with rationale |
| `Context` | A situational frame or active constraint |
| `Observation` | Something noticed — hardens into Concept over time |

Edge types: `GOVERNS`, `DERIVES_FROM`, `CONSTRAINS`, `DEFINES`, `INSTANTIATES`, `RESONATES_WITH`, `OPPOSES`, `SUPERSEDES`, `ANCHORS`.

Every edge carries a `rationale` — the reason it was drawn. Without rationale, it's topology without history.

### Seed geometry

Every graph is initialised with the phrase `geometry governs exploration`:

```
Seed → geometry → exploration
           ↓           ↓
        governs    derives_from
           ↓
      (self-defining)
```

The seed is immutable. All `depth` values on Concept nodes are measured from it.

---

## Inference

`preambulate infer` parses Python source and writes graph edges:

- **Phase 1** — file-level `DERIVES_FROM` edges from import statements
- **Phase 2** — symbol extraction: functions, classes, methods become `Artifact` nodes; files `GOVERN` their symbols
- **Phase 3** — within-file call edges between symbol artifacts
- **Phase 4** — cross-file call edges (requires symbol index built from prior inference)

Run after significant changes, or let the `PostToolUse` hook handle it incrementally.

---

## Clustering

```bash
preambulate cluster          # Phase A: file-level communities
preambulate cluster --phase B  # Phase B: symbol-level communities
preambulate cluster --reset    # Recompute, supersede old clusters
```

Clustering uses label propagation over the import/call graph. Package `__init__.py` files are excluded from Phase A to prevent gravity wells.

---

## Session end

Before your final message each session:

```bash
preambulate decision \
    --label "What was done" \
    --rationale "Why the key choices were made" \
    --touched "path/to/file.py,other/file.py"
```

Then propose 1–2 semantic edges if anything earned its place in the graph:

```bash
preambulate decision \
    --concept "concept-label|Definition of the concept" \
    --edge "file.py|INSTANTIATES|concept-label" \
    --edge-rationale "file.py is the concrete implementation"
```

---

## Briefing

```bash
preambulate briefing                          # recent activity
preambulate briefing --focal path/to/file.py  # proximity mode
```

Proximity mode shows the decision history and graph neighborhood around a specific file or concept.

---

## Security

`memory.db/` contains your full project reasoning history — every decision, rationale, and file path. Treat it like source code:

- It is excluded from git by default (`.gitignore` written on `preambulate init`)
- Do not commit it, share it, or store it in an untrusted location
- The database is not encrypted at rest — a future release will add at-rest encryption

---

## Migration

When the schema changes:

```bash
preambulate export dump
preambulate export restore --dump graph_export.json --reset
```

`--reset` drops and reinitialises the database before restoring — no separate `init --reset` step needed.

---

## Requirements

- Python 3.10+
- [Claude Code](https://claude.ai/code)
