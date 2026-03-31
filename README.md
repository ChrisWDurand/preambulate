# preambulate

Want to introduce the tool?

Claude forgets everything between sessions. Every morning you re-explain the architecture. Every new team member asks questions the codebase already answered. Every agent you spawn starts blind.

You're not losing code. You're losing reasoning.

**Preambulate fixes that.**

It's a graph-based memory system for Claude Code. It runs locally, travels with your project, and remembers what you decided and why — not just what the code does, but the reasoning behind it.

Every session starts with a briefing. Not a wall of text — a structured summary of what was touched, what was decided, and what shaped the code you're about to work on. Claude reads it before the first message. You pick up exactly where you left off.

Every decision gets recorded. Not by you — by the hooks. When Claude edits a file, the graph captures it. When you end a session, Claude proposes what mattered and you confirm. The rationale lives in the graph, connected to the code it shaped.

Every agent you spawn inherits the context. Sub-agents don't start blind. They query the graph, find the relevant decisions, understand the constraints. You stop being the explainer and start being the architect.

**What you get:**
- Sessions that resume instead of restart
- Agents that know why the code is the way it is
- A project memory that compounds over time
- Sync across machines and teammates via preambulate.dev

**What it costs:**
Nothing to start. Install it, use it locally, see the difference.

```bash
pipx install preambulate
preambulate install
```

Two commands. Your next session starts informed.

On first use, Claude Code will prompt you to approve the hook commands. Approve them — this is a one-time step.

[preambulate.dev](https://preambulate.dev) — sign in with GitHub, get your API key, sync everywhere.

**Your agents forget. Preambulate doesn't.**

---

## How it works

### Hooks

Three hooks fire automatically once `preambulate install` has run:

| Hook | Trigger | What it does |
|---|---|---|
| `SessionStart` | Session opens | Creates a session Decision node; prints briefing |
| `PostToolUse` (Write/Edit) | File saved | Records the file as an Artifact; infers import edges |
| `Stop` | Each response | Pushes to remote (no-op if sync not configured) |

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

Every edge carries a `rationale` — the reason it was drawn.

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
| `preambulate sync` | Push or pull the graph to/from preambulate.dev |
| `preambulate export` | Dump or restore the full graph to/from JSON |
| `preambulate mcp` | Start the MCP server (stdio transport) |

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

## Inference

`preambulate infer` parses Python source and writes graph edges:

- **Phase 1** — file-level `DERIVES_FROM` edges from import statements
- **Phase 2** — symbol extraction: functions, classes, methods become `Artifact` nodes; files `GOVERN` their symbols
- **Phase 3** — within-file call edges between symbol artifacts
- **Phase 4** — cross-file call edges (requires symbol index built from prior inference)

---

## Clustering

```bash
preambulate cluster            # Phase A: file-level communities
preambulate cluster --phase B  # Phase B: symbol-level communities
preambulate cluster --reset    # Recompute, supersede old clusters
```

---

## Sync

```bash
preambulate sync save-key <your-key>  # save API key from preambulate.dev
preambulate sync push                 # push graph to remote
preambulate sync pull                 # pull and merge remote graph
```

Sign in at [preambulate.dev](https://preambulate.dev) to get your API key. Sync is a no-op without one — the local graph works fully without it.

---

## Security

`memory.db/` contains your full project reasoning history. It is excluded from git by default (`.gitignore` written on `preambulate init`). Do not commit it or store it in an untrusted location.

---

## Migration

When the schema changes:

```bash
preambulate export dump
preambulate export restore --dump graph_export.json --reset
```

---

## Requirements

- Python 3.10+
- [Claude Code](https://claude.ai/code)
