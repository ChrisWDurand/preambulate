# Preambulate → Host Environment Auth Contract

_Owner: preambulate (this repo)_
_Schema version: 2.0_
_Last reconciled: 2026-03-30_

This file defines what preambulate requires from the host environment for
`PREAMBULATE_API_KEY` to be available across all execution contexts —
interactive shells, non-interactive subshells, and coordinator-initiated
subprocess spawns.

---

## The Problem

`~/.bashrc` is sourced only in interactive shells. A guard of the form:

```bash
case $- in
    *i*) ;;
    *) return;;
esac
```

prevents any `export` below it from running in non-interactive contexts.
When the coordinator spawns a subprocess (`claude --print ...`), or when
hooks fire (SessionStart, PostToolUse, Stop), the shell is non-interactive.
The key is absent. Sync operations fail with a 401.

---

## Required Environment Configuration

`PREAMBULATE_API_KEY` must be exported **above** any non-interactive guard
in `~/.bashrc`, or set in a file sourced unconditionally:

**Option A — above the guard in `~/.bashrc`:**
```bash
export PREAMBULATE_API_KEY=prm_live_...
# --- below this line: non-interactive guard ---
case $- in
    *i*) ;;
    *) return;;
esac
```

**Option B — `~/.profile` or `~/.bash_profile`:**
```bash
export PREAMBULATE_API_KEY=prm_live_...
```
`~/.profile` is sourced for login shells regardless of interactivity.

**Option C — `/etc/environment` (WSL / system-wide):**
```
PREAMBULATE_API_KEY=prm_live_...
```
Available to all processes without shell sourcing. Requires root.
On WSL, changes take effect after reopening the terminal.

---

## What Preambulate Requires

The key must be present in `os.environ` at the time any sync command runs.
No fallback — if absent, sync prints the 401 message and aborts cleanly.

Coordinator-initiated spawns (`cd <repo> && claude --print ...`) inherit
the coordinator's environment. If the key is set correctly in the
coordinator's shell, it propagates to all spawned agents automatically.

---

## Coordinator Loop Behavior

When the coordinator closes the loop autonomously (pull → validate → push),
key absence at any step must not silently corrupt state:

| Step | Key absent | Behavior |
|---|---|---|
| `sync pull` | No key | Abort pull, print message, continue session |
| `sync push` | No key | Abort push, print message — **graph changes are local only** |
| Spawn subprocess | Key inherited | If coordinator's shell has the key, subagents inherit it |

The coordinator must not proceed with a push after a failed pull — local
graph may be behind remote. Pull failure should surface to the Architect
before the coordinator writes decisions.

---

## Registration Flow

When a 401 is received:

1. Print: `invalid API key — run 'preambulate sync register' to get a new key`
2. `preambulate sync register` opens `https://preambulate.dev` (or prints URL if headless)
3. User authenticates via GitHub OAuth, receives new `prm_live_` key
4. User updates `PREAMBULATE_API_KEY` at the correct location (above the guard)
5. New key is active immediately in the current shell; new subshells inherit it

**Note:** `export PREAMBULATE_API_KEY=<new-key>` in the current shell does
not persist across sessions. The persistent fix is Option A, B, or C above.

---

## Known Issue — Current Session Behavior

After `source ~/.bashrc` in a shell where the key was already exported,
the new value does not override the existing export. The shell holds the
original value. To apply a rotated key in the current shell:

```bash
export PREAMBULATE_API_KEY=<new-key>
```

`source` is only effective when the variable was not previously exported
in the current session.

---

## Open Questions for v2

**Keychain integration**
On macOS, `PREAMBULATE_API_KEY` could be stored in Keychain and read via
`security find-generic-password`. On WSL/Linux, `secret-tool` or a `.env`
file with restricted permissions (0o600) are alternatives.
Coordinator loop would call `keystore.load_api_key()` rather than reading
`os.environ` directly.
