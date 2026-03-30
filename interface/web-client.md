# Preambulate Client → Web Interface Contract

_Owner: preambulate (this repo)_
_Counterpart: preambulate-web/interface/client-web.md_
_Schema version: 2.0_
_Last reconciled: 2026-03-29_

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

Push is incremental — only nodes/edges since last push. Server merges,
does not replace. First-write-wins on id collision. Edge dedup key:
`rel|from_type|to_type|from_id|to_id`.

**Cluster nodes** are included in incremental pushes. The server stores
them as opaque JSON alongside other node types — no special handling required.

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

Called by `preambulate sync rotate`. Body: `{ "project": "<name>" }`.

Expected responses: `200` (rotated), `401`, `404` (no prior key on server).

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

v1 is single-tenant. R2 path: `projects/${project}/graph.json`.

v2 plan: `users/${userId}/projects/${project}/graph.json`
with userId derived from a per-user key lookup table.

---

## Agent Spawning Protocol (proposed — under negotiation with preambulate-web)

When a preambulate session has work that requires a response from preambulate-web,
it should not require the user to manually open the other repo and relay context.
The graph is the communication channel. The sync backend is the transport.

### Message convention

An inter-agent message is a `Decision` node with:
- `decision_type: "agent_message"` (new value — requires schema update)
- `label`: short description of what is needed
- `rationale`: full context, including which question in the contract requires action
- `session_id`: originating session
- `machine_id`: originating repo/machine identity

The Decision is anchored (`ANCHORS`) to the contract `Artifact` node it concerns.

On push, the message reaches the sync backend. On the receiving agent's next
`SessionStart`, `preambulate capture` surfaces it in the briefing under a new
section: **"Messages from other agents"** — distinct from regular decisions.

### Contract Artifact convention

For an anchor to be meaningful across repos, both graphs must contain an
`Artifact` node for the contract file. The path used as the anchor must be
agreed between both sides. Proposed canonical paths:

| Contract file | Canonical artifact path |
|---|---|
| This file | `interface/web-client.md` |
| Counterpart | `interface/client-web.md` |

Each repo registers its own contract artifact. Cross-repo references use the
canonical path as a shared key — not a UUID, since UUIDs differ per graph.

### Subgraph delivery

A spawned agent does not need the full graph — it needs the neighborhood around
the contract node. The existing `preambulate briefing --focal interface/web-client.md`
query serves this purpose. No new transport is needed.

### What preambulate-web needs to provide

- Confirm `decision_type: "agent_message"` is acceptable as an opaque value
  (server stores it without inspection — no change required server-side)
- Confirm the briefing pull on SessionStart will surface new Decision nodes
  of this type prominently (client-side change in `capture.py` / `briefing.py`)
- Agree on canonical contract artifact paths (table above)

### Open design question

**Spawn trigger**: today, someone must open preambulate-web for the message to
be acted on. A future improvement: the Stop hook could POST a lightweight
notification (project name + contract path) to a `/notify` endpoint, which
the preambulate-web server stores. The next SessionStart in that repo pulls
the notification before running capture. No new infrastructure — one new
endpoint on the existing Worker.

Propose design in `preambulate-web/interface/client-web.md`.

---

## Open Questions

**Q10: Key validation and re-registration**
When a client holds a key that the server rejects with 401, there is no defined
recovery path short of re-authenticating via the signup page. The client has no
way to know whether the key was revoked, never registered, or is simply malformed.

Two things needed:
- A `GET /auth/validate` endpoint (or equivalent) the client can call to confirm
  its key is recognized — distinct from a push/pull so the failure message is
  unambiguous.
- A defined re-registration flow: either a CLI command (`preambulate sync register`)
  that opens the signup URL, or a way for the server to communicate a key refresh
  without requiring a browser.

Propose a design in `preambulate-web/interface/client-web.md`.

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
