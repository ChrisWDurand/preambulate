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

A `SessionStart` hook in `.claude/settings.json` runs `capture.py` at the start of every Claude Code session. It creates a `Decision` node and wires it to the `geometry` Concept node via an `ANCHORS` edge. The hook reads `CLAUDE_PROJECT_DIR` and `CLAUDE_SESSION_ID` from the environment.

If `memory.db` does not exist, `capture.py` skips silently — run `init.py` first.

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
| `capture.py` | SessionStart hook handler |
| `.claude/settings.json` | Hook registration |
| `requirements.txt` | `kuzu` dependency |
