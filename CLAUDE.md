# Preambulate

Graph-based project memory for Claude Code. Every session is captured as a Decision node anchored to the seed geometry in a local Kuzu database.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
python init.py          # creates memory.db and inserts seed geometry
```

Use `python init.py --reset` to drop and recreate the database.

## Session capture

A `SessionStart` hook runs `capture.py` at the start of every session. It creates a `Decision` node anchored to the `geometry` Concept node.

A `PostToolUse` hook runs `artifact.py` after every `Write` or `Edit` tool call. It creates an `Artifact` node for the file (if new) and a `Decision` node recording the edit, anchored together. No action required — this is automatic.

If `memory.db` does not exist both scripts skip silently — run `init.py` first.

## Session end

Before your final message each session, record a Decision node summarizing the work:

```
python decision.py \
    --label "<one-line summary of what was done>" \
    --rationale "<why the key choices were made>" \
    --touched "<comma-separated relative paths of files edited>"
```

Example:
```
python decision.py \
    --label "Add PostToolUse artifact hook" \
    --rationale "Needed automatic write path so graph grows without manual intervention" \
    --touched "artifact.py,.claude/settings.json,CLAUDE.md"
```

Run from the project root. If no decisions were made and no files were edited, skip it.

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
| `init.py` | DB init + seed geometry |
| `capture.py` | SessionStart hook — writes session Decision node |
| `artifact.py` | PostToolUse hook — writes Artifact + Decision on file edit |
| `decision.py` | Claude-callable — writes session-end Decision node |
| `.claude/settings.json` | Hook registration |
| `requirements.txt` | `kuzu` dependency |
