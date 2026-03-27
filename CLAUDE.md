# Preambulate

Graph-based project memory for Claude Code. Every session is captured as a Decision node anchored to the seed geometry in a local Kuzu database.

## Setup

```bash
pipx install preambulate   # or: pipx install -e . for editable installs
preambulate install        # writes hooks to ~/.claude/settings.json (global, one-time)
```

That's the full setup. The `install` command merges the `SessionStart` and `PostToolUse` hooks into your global Claude Code settings so preambulate works in every repo. It is safe to run more than once — commands already present are skipped.

Per-project hook files (`.claude/settings.json`) are also supported and take the same format if you prefer repo-scoped hooks.

`preambulate init` is no longer required. If `memory.db` does not exist when a session starts, `preambulate capture` runs `init` automatically.

Use `preambulate init --reset` to drop and recreate an existing database.

For schema migrations: `preambulate export dump`, then `preambulate init --reset`, then `preambulate export restore --dump graph_export.json`.

**CLAUDE.md is yours.** Use it for project architecture, conventions, and system design. Preambulate's own behavioral instructions ship through the MCP server automatically — they arrive as server instructions at the start of every session without requiring any entry here.

## Session capture

Three hooks fire automatically when installed via `preambulate install`:

| Hook | Trigger | Command | What it does |
|------|---------|---------|-------------|
| `SessionStart` | Session open | `preambulate capture` | Auto-inits `memory.db` if missing; creates session Decision node; prints briefing |
| `SessionStart` | Session open | `preambulate sync pull` | Merges remote graph into local (runs after capture) |
| `PostToolUse` | `Write` or `Edit` | `preambulate artifact` + `preambulate infer` | Records file edits as Artifact nodes; infers DERIVES_FROM edges from imports |
| `Stop` | Each response | `preambulate sync push` | Pushes incremental local changes to remote (no-op if `PREAMBULATE_API_KEY` unset) |

Sync hooks are no-ops when `PREAMBULATE_API_KEY` is not set — they skip cleanly without blocking the session.

## Session end

Before your final message each session, run two steps:

### Step 1 — Record the Decision node

```
preambulate decision \
    --label "<one-line summary of what was done>" \
    --rationale "<why the key choices were made>" \
    --touched "<comma-separated relative paths of files edited>"
```

Skip if nothing was decided and no files were edited.

### Step 2 — Propose semantic edges

After recording the Decision, propose 1–2 semantic edges based on what was built
this session.  Keep suggestions concrete and earned — not exhaustive.

Edge types to consider:
- `INSTANTIATES` — a file is the concrete implementation of a concept
- `DERIVES_FROM` — a file or concept logically descends from another (beyond imports)
- `RESONATES_WITH` — two nodes occupy structurally similar positions in the graph

Present suggestions to the user before writing them:

> Suggest: `briefing.py -[INSTANTIATES]-> memory-briefing`
> ("Formatted output of graph queries shown at session start")
> Accept?

If the user confirms, write the edge.  New concepts are created automatically
at depth 1.  Existing nodes are matched by file path (Artifact) or label (Concept).

```
preambulate decision \
    --concept "memory-briefing|Formatted output of graph queries shown at session start" \
    --edge "briefing.py|INSTANTIATES|memory-briefing" \
    --edge-rationale "briefing.py is the concrete implementation of the session-start briefing"
```

`--concept` and `--edge` are repeatable.  Concepts must be declared before edges that
reference them (within the same call, or in a prior call).

Skip the edge step if nothing earned its place in the graph this session.

## Schema

Defined in `schema.cypher` (DDL) and `schema.spec.md` (spec). Node types: `Seed`, `Concept`, `Artifact`, `Context`, `Observation`, `Decision`. Relationship types use `CREATE REL TABLE GROUP` because most support multiple FROM-TO node type pairs.

Kuzu constraints:
- No `NOT NULL` support — omit entirely
- One statement per `conn.execute()` call
- Strip comment lines before splitting on `;`

## Files

| File | Purpose |
|------|---------|
| `schema.cypher` | Kuzu DDL |
| `schema.spec.md` | Language-agnostic schema spec |
| `pyproject.toml` | Package definition and `preambulate` entry point |
| `preambulate/` | Python package — all CLI modules live here |
| `preambulate/cli.py` | Dispatcher: routes `preambulate <cmd>` to the right module |
| `preambulate/init.py` | DB init + seed geometry |
| `preambulate/install.py` | Writes hooks to `~/.claude/settings.json` (global one-time setup) |
| `preambulate/capture.py` | SessionStart hook — auto-inits db if missing, writes session Decision, prints briefing |
| `preambulate/artifact.py` | PostToolUse hook — writes Artifact + Decision on file edit |
| `preambulate/infer.py` | PostToolUse hook — infers DERIVES_FROM edges from Python imports |
| `preambulate/briefing.py` | Query module — `query_briefing(conn, session_id, focal_node=None)` |
| `preambulate/decision.py` | Claude-callable — writes session-end Decision and/or semantic edges |
| `preambulate/sync.py` | Sync command — push/pull graph snapshot via api.preambulate.dev/sync |
| `preambulate/export.py` | Migration tool — dump/restore full graph to/from JSON |
| `.claude/settings.json` | Hook registration |
| `requirements.txt` | Dev dependency: `kuzu` (production deps are in `pyproject.toml`) |
