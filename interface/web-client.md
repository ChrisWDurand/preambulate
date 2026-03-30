# Preambulate Client → Web Interface Contract

_Owner: preambulate (this repo)_
_Counterpart: preambulate-web/interface/client-web.md_
_Schema version: 2.0_
_Last reconciled: 2026-03-29 (WSL migration complete; spawn mechanism confirmed)_

This file defines what the preambulate client requires from the web backend.
Read alongside `preambulate-web/interface/client-web.md` for the full picture.

---

## Auth — v1 Single-Tenant Model

v1 is single-user. One `API_KEY` Cloudflare Worker secret is the user boundary.
No per-user key generation, rotation, or lookup table exists.

The client reads `PREAMBULATE_API_KEY` from the environment and sends:
```
Authorization: Bearer {api_key}
```

Missing or wrong key → `401`.

Per-user keys and multi-tenant namespacing are deferred to v2.

---

## Encryption

Push payloads are encrypted client-side with a Fernet key stored at
`~/.preambulate/{project_id}.key` (0o600). The server stores opaque
ciphertext only — it cannot read graph content.

Push `Content-Type`: `application/octet-stream` (encrypted binary).
Pull response body: opaque ciphertext, decrypted client-side after receipt.

Key rotation: `preambulate sync rotate` — pull → POST /keys/rotate →
replace local key → push full graph re-encrypted. API key and encryption
key rotate atomically from the client's perspective.

---

## Request Headers (all requests)

| Header | Example | Required |
|---|---|---|
| `Authorization` | `Bearer prm_live_abc123` | Yes — 401 if missing/wrong |
| `X-Preambulate-Project` | `myapp` | Yes — 400 if missing |
| `X-Preambulate-Schema` | `2.0` | Yes — 409 if wrong value |
| `X-Preambulate-Machine` | `a1b2c3d4-...` | No — ignored in v1, reserved for agent auth in v2 |
| `X-Preambulate-Timestamp` | `2026-03-29T00:00:00Z` | No |
| `User-Agent` | `preambulate-client/1.0` | No |

---

## Endpoints

### Push — `POST /sync?op=push`

`Content-Type: application/octet-stream` (encrypted payload)

Decrypted body shape:
```json
{
  "version": "2.0",
  "nodes": {
    "Decision":    [ { "id": "uuid", ... } ],
    "Artifact":    [ { "id": "uuid", ... } ],
    "Cluster":     [ { "id": "uuid", "label": "...", "phase": "A|B", ... } ],
    "Concept":     [ { "id": "uuid", ... } ],
    "Observation": [ { "id": "uuid", ... } ],
    "Context":     [ { "id": "uuid", ... } ]
  },
  "edges": [
    { "rel": "ANCHORS", "from_type": "Decision", "to_type": "Artifact", "from_id": "uuid", "to_id": "uuid", ... }
  ]
}
```

**Push is a full replace.** The server stores incoming bytes verbatim via
`R2.put()` — no merge, no dedup. If the client sends a delta, the remote
graph is replaced with only that delta. All merge logic is client-side.

Correct push cycle:
1. Pull current remote (decrypt locally)
2. Merge remote into local graph (`merge_remote()`)
3. Dump full local graph (`dump_since(conn, None)`)
4. Encrypt and push

The `--full` flag on `preambulate sync push` is currently the only safe
mode. Incremental push (`dump_since(conn, since)`) is unsafe until the
pull-merge-push cycle is implemented. **This is a known bug — tracked.**

Cluster nodes are included in the full dump and transfer correctly.

**Expected responses:**

| Status | Meaning | Client behavior |
|---|---|---|
| `200` | Accepted | Record push timestamp |
| `400` | Missing required header or bad op | Print error body |
| `401` | Bad or missing API key | Print "Invalid API key — check PREAMBULATE_API_KEY" |
| `402` | Account not authorized | Print "Sync not authorized — visit preambulate.dev to activate your account" |
| `409` | Schema mismatch | Print `expected` field from body, abort |
| `413` | Payload too large | Print `max_bytes` from body, abort |

---

### Pull — `GET /sync?op=pull&project={name}`

Response body: encrypted ciphertext. Client decrypts and merges into local db.

**Expected responses:**

| Status | Meaning | Client behavior |
|---|---|---|
| `200` | Graph returned | Decrypt, merge into local db |
| `404` | No remote graph yet | No-op — normal for new projects |
| `400` | Missing project / bad op | Print error body |
| `401` | Bad or missing API key | Print actionable message, abort |
| `402` | Account not authorized | Print actionable message, abort |
| `409` | Schema mismatch | Print `expected` field from body, abort |

Includes `ETag` header (reserved — not yet used for conditional requests).

---

### Key Rotation — `POST /keys/rotate`

Called by `preambulate sync rotate`. No request body — auth via Bearer token only.

Expected responses: `200` with new key in body, `401` (revoked/invalid key).

---

## Payload Size

Server enforces **10 MB** — checked on `Content-Length` before reading body.
Returns `413` with:
```json
{ "error": "payload too large", "max_bytes": 10485760 }
```

Client guard matches: pushes over 10 MB are rejected before sending.

---

## Schema Version

Current version: `2.0`. Server stores all graphs at `"version": "2.0"`.

Schema mismatch returns `409`:
```json
{ "error": "Schema version mismatch", "expected": "2.0" }
```

---

## Storage Namespacing

v1 is single-tenant. R2 path: `projects/${project}/graph.bin`.

v2 plan: `users/${userId}/projects/${project}/graph.json`
with userId derived from a per-user key lookup table.

---

## Agent Spawning Protocol

When a preambulate session has work that requires a response from preambulate-web,
it should not require the user to manually open the other repo and relay context.
The graph is the communication channel. The sync backend is the transport.

### Team model

Two agent patterns are supported:

**Subagent** — spawned agent sends a contract proposal directly to the coordinator.
Coordinator validates via graph traversal and commits if approved.

**Team** — spawned agents negotiate a proposal between themselves first, reach
agreement, then send the agreed proposal to the coordinator. The coordinator
validates the agreed change and commits. Use this when both sides own part of
the interface (e.g. preambulate + preambulate-web on this contract).

In both cases the **coordinator is the single writer** to contract files.

**Role hierarchy:**
- **Architect** — sets direction; the coordinator escalates to the Architect only on genuine conflicts or decisions requiring human authority
- **Coordinator** — validates proposals via graph traversal, writes to the graph, closes the loop autonomously when no conflicts are found
- **Spawned agents / teams** — execute tasks, submit proposals as output; do not write to the graph

**Validation model:** The coordinator uses the graph as a spatial index — traversing anchored decisions, edge relationships, and rationale chains to identify the relevant region of the codebase, then reading only that region. The graph narrows search to regions of interest; it does not replace reading code. This is why every decision has rationale and every edge has a reason: they make the graph's geometry precise enough for targeted validation.

### Proposal convention

A contract proposal is a `Decision` node with:
- `decision_type: "contract_proposal"` — teammate signals a proposed change
- `decision_type: "contract_agreed"` — counterpart has accepted; ready for coordinator
- `label`: short description of what is proposed
- `rationale`: full context and which contract section is affected
- `session_id`: originating session
- `machine_id`: originating repo/machine identity

The Decision is anchored (`ANCHORS`) to the contract `Artifact` node it concerns.
Coordinator picks up `"contract_agreed"` nodes, not raw proposals.

Spawned agents surface proposals to the coordinator as output. The coordinator
validates via graph traversal and writes to the graph. Subagents do not write to
the graph directly. The coordinator is the sole graph writer in all patterns.

### Contract Artifact convention

Both graphs must contain an `Artifact` node for both contract files. The canonical
path is the shared key — not a UUID, since UUIDs differ per graph.

| Contract file | Canonical artifact path |
|---|---|
| This file | `interface/web-client.md` |
| Counterpart | `interface/client-web.md` |

### Subgraph delivery

A spawned agent does not need the full graph — it needs the neighborhood around
the contract node. The existing `preambulate briefing --focal interface/web-client.md`
query serves this purpose. No new transport is needed.

### Spawn mechanism

```
cd ~/source/repos/preambulate-web && claude --print "<prompt>"
```

Prompt payload (three parts):
1. Path to preambulate's interface contract
2. Path to preambulate-web's interface contract
3. `preambulate briefing --focal interface/web-client.md` — plaintext graph
   context targeting the contract neighborhood; does not cross encryption boundary

Repos must be on the native WSL filesystem for path and subprocess reliability.
Migration complete as of 2026-03-29.

### Spawn trigger

The coordinator initiates spawning directly — runs `claude` as a subprocess in
the target repo. No human needs to open the other repo. The spawned agent reads
context via `preambulate sync pull` + `preambulate briefing --focal <contract>`
and acts under protocol. The `/notify` endpoint plan is superseded by direct
coordinator-initiated spawning.

### Responses to preambulate-web spawning questions (2026-03-29)

**WSL path:** Migration complete as of 2026-03-29. Both repos now at
`~/source/repos/preambulate` and `~/source/repos/preambulate-web` on the native
WSL filesystem. Spawning protocol can proceed.

**Spawn mechanism:** Accepted. `cd ~/source/repos/preambulate-web && claude --print "<prompt>"` (`--cwd` flag does not exist in the claude CLI)
is the spawn call. Three-part payload: both contract paths + `preambulate briefing
--focal interface/web-client.md` output as plaintext context.

**Completion signal:** Prefer **contract timestamp** — update `_Last reconciled:`
when the spawned agent finishes. No new files needed. Git commit is implicit.

---

## Open Questions

**Q10: Key validation and re-registration**
_Answered by preambulate-web 2026-03-29._

No `/auth/validate` endpoint — the next push/pull provides the 401 signal.

Client behavior on 401:
- Print: `"API key rejected — run 'preambulate sync register' to get a new key"`
- `preambulate sync register` opens `https://preambulate.dev` in the browser
  (or prints the URL if headless). User re-authenticates via GitHub OAuth,
  receives new `prm_live_` key, updates `PREAMBULATE_API_KEY`.

**preambulate:** `preambulate sync register` implemented — opens `https://preambulate.dev`
in browser (or prints URL if headless). No server change required.

---

## Open Questions for v2

**Agent authorization**
One API key should not authorize unlimited agent children. Design options:
- Agents share a push quota with the parent key
- Agents get sub-keys with individual limits
`X-Preambulate-Machine` (currently ignored) is the natural identity carrier.
Propose a design in `preambulate-web/interface/client-web.md`.

**Multi-device session management**
Last-sign-in-wins: each new GitHub OAuth sign-in revokes all previous keys.
A user authenticating from a second device loses their first device's key
silently. Needs session management or a warning on the key display page.

---

## Resolved Questions

| Question | Answer |
|---|---|
| Storage collision risk | Not applicable in v1 — single tenant |
| Per-user keys | v2 |
| Rate limiting | Cloudflare edge rule, 120 pushes/hour per API key |
| X-Preambulate-Machine server use | Ignored in v1, reserved for agent authorization in v2 |
| 403 per-project auth | v2 |
| Schema mismatch status code | 409 — confirmed |
| Signup page | Built and deployed — GitHub OAuth, prm_live_ key |
| Payment gating | is_authorized flag in D1, 402 response, edge rate limiting |
| Agent authorization | Sub-key model, deferred to v2 |
| Payload encryption | Fernet, client-side only — server stores opaque ciphertext |
| Cluster nodes in payload | Included in push/pull as of 2026-03-29 — server handles as opaque JSON |
| Sync validation (2026-03-29) | Full push: 392 nodes, 620 edges transferred. Pull: 392 nodes recognized, 0 duplicated. Round-trip clean. |
| source ~/.bashrc key override | `source ~/.bashrc` does not override an already-exported variable in the current shell. Re-registration flow must account for this — see Q10. |
