# Preambulate Client → Web Interface Contract

_Owner: preambulate (this repo)_
_Counterpart: preambulate-web/interface/client-web.md_
_Schema version: 2.0_
_Last reconciled: 2026-03-27_

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

## Request Headers (all requests)

| Header | Example | Required |
|---|---|---|
| `Authorization` | `Bearer prm_live_abc123` | Yes — 401 if missing/wrong |
| `X-Preambulate-Project` | `myapp` | Yes — 400 if missing |
| `X-Preambulate-Schema` | `2.0` | Yes — 409 if wrong value |
| `X-Preambulate-Machine` | `a1b2c3d4-...` | No — fully ignored by server in v1 |
| `X-Preambulate-Timestamp` | `2026-03-27T22:00:00Z` | No |
| `User-Agent` | `preambulate-client/1.0` | No |

---

## Endpoints

### Push — `POST /sync?op=push`

Additional header: `Content-Type: application/json`

Body: JSON with shape:
```json
{
  "nodes": {
    "Decision": [ { "id": "uuid", ... } ],
    "Artifact": [ { "id": "uuid", ... } ]
  },
  "edges": [
    { "type": "ANCHORS", "from": "uuid", "to": "uuid", ... }
  ]
}
```

Push is incremental — only nodes/edges since last push. Server merges,
does not replace. First-write-wins on id collision. Edge dedup key:
`rel|from_type|to_type|from_id|to_id`.

**Expected responses:**

| Status | Meaning | Client behavior |
|---|---|---|
| `200` | Accepted | Record push timestamp |
| `400` | Missing required header or bad op | Print error body |
| `401` | Bad or missing API key | Print "Invalid API key — check PREAMBULATE_API_KEY" |
| `409` | Schema mismatch | Print `expected` field from body, abort |
| `413` | Payload too large | Print `max_bytes` from body, abort |

---

### Pull — `GET /sync?op=pull&project={name}`

**Expected responses:**

| Status | Meaning | Client behavior |
|---|---|---|
| `200` | Graph returned | Merge into local db |
| `404` | No remote graph yet | No-op — normal for new projects |
| `400` | Missing project / bad op | Print error body |
| `401` | Bad or missing API key | Print actionable message, abort |
| `409` | Schema mismatch | Print `expected` field from body, abort |

Pull response body (200): full merged graph as JSON. Includes `ETag` header
(not enforced for conditional requests yet, reserved for future use).

---

## Payload Size

Server enforces **10 MB** — checked on `Content-Length` before reading body,
then on actual byte count. Returns `413` with:
```json
{ "error": "payload too large", "max_bytes": 10485760 }
```

Client guard is set to match: pushes over 10 MB are rejected before sending.

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
Project name must be treated as globally unique within the deployment.

v2 plan (not yet built): `users/${userId}/projects/${project}/graph.json`
with userId derived from a per-user key lookup table.

---

## Questions for preambulate-web

_Answer these in `preambulate-web/interface/client-web.md`._

**Q7: Signup page status**
Is the signup page built and deployed? Can a new user visit it, authenticate
via GitHub OAuth, and receive a `prm_live_` API key? If not, what's missing?

---

## Resolved Questions

| Question | Answer |
|---|---|
| Storage collision risk | Not applicable in v1 — single tenant |
| Per-user keys | v2 |
| Rate limiting | None in v1 — Cloudflare free tier is practical ceiling |
| X-Preambulate-Machine server use | Fully ignored in v1, reserved for future |
| 403 per-project auth | v2 |
| Schema mismatch status code | Already 409 — confirmed correct |
