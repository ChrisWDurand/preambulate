# Preambulate Client → Web Interface Contract

_Owner: preambulate (this repo)_
_Counterpart: preambulate-web/interface/client-web.md_
_Schema version: 2.0_

This file defines what the preambulate client requires from the web backend.
The preambulate-web team owns the counterpart file defining what they require from the client.

---

## Authentication

The client sends an API key on every request:

```
Authorization: Bearer {api_key}
```

- The client reads the key from the `PREAMBULATE_API_KEY` environment variable.
- The client has no opinion on key format. Recommend a prefixed token (`prm_live_...`) so users can recognize it.
- One key per user. Project namespacing is handled by the `X-Preambulate-Project` header, not the key.

---

## Request Headers (all requests)

| Header | Example | Notes |
|---|---|---|
| `Authorization` | `Bearer prm_live_abc123` | Required |
| `X-Preambulate-Project` | `myapp` | Directory name of the project root |
| `X-Preambulate-Machine` | `a1b2c3d4-...` | Stable UUID per machine, from identity.py |
| `X-Preambulate-Timestamp` | `2026-03-27T22:00:00Z` | ISO 8601 UTC |
| `X-Preambulate-Schema` | `2.0` | Sync payload schema version |
| `User-Agent` | `preambulate-client/1.0` | |

---

## Endpoints

### Push — `POST /sync?op=push`

Additional header: `Content-Type: application/json`

Body: JSON object with shape:
```json
{
  "nodes": {
    "Decision": [ { "id": "uuid", ... } ],
    "Artifact": [ { "id": "uuid", ... } ],
    "Concept":  [ { "id": "uuid", ... } ]
  },
  "edges": [
    { "type": "ANCHORS", "from": "uuid", "to": "uuid", ... }
  ]
}
```

Push is incremental — only nodes/edges created since the last successful push are sent.
The server must merge, not replace.

**Expected responses:**

| Status | Meaning | Client behavior |
|---|---|---|
| `200` | Push accepted | Record push timestamp, continue |
| `401` | Bad or missing API key | Print "Invalid API key — check PREAMBULATE_API_KEY", abort |
| `409` | Schema version mismatch | Print expected version from response body, abort |
| `413` | Payload too large | Print size limit from response body, abort |
| `429` | Rate limited | Print retry guidance, do not record as error |

---

### Pull — `GET /sync?op=pull&project={name}`

No body.

**Expected responses:**

| Status | Meaning | Client behavior |
|---|---|---|
| `200` | Graph returned | Merge into local db |
| `404` | No remote graph for this project yet | No-op — normal for new projects |
| `401` | Bad or missing API key | Print actionable message, abort |
| `409` | Schema version mismatch | Print expected version, abort |
| `429` | Rate limited | Print retry guidance, do not record as error |

Pull response body (200): same JSON shape as push body — full graph, not incremental.

---

## Payload Size

Client guards at 100 MB before sending. Server may enforce a lower limit and return 413.
If the server limit differs from 100 MB, it should be documented in `client-web.md`
so the client guard can be adjusted to match.

Current server limit: 10 MB (per preambulate-web, subject to change).

---

## Storage Namespacing

The client sends `X-Preambulate-Project` as the project identifier.
The server is responsible for namespacing storage by user so two users with a project
named `"myapp"` do not collide. Current R2 path (per preambulate-web):
`projects/${userId}/${project}/graph.json`.

---

## Schema Version Policy

`X-Preambulate-Schema: 2.0` is the current version.

If the server does not support this version, it must return `409` with a body indicating
the supported version:
```json
{ "error": "schema_mismatch", "supported": "2.0", "received": "3.0" }
```

The client will surface this message to the user.

---

## Open Questions (pending preambulate-web response)

- Is `X-Preambulate-Machine` used server-side for anything, or just logged?
- What is the rate limit policy (requests per minute per key)?
- Will 403 per-project authorization be implemented in v1 or v2?
