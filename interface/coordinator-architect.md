# Coordinator → Architect Interface Contract

_Owner: preambulate (this repo)_
_Schema version: 2.0_
_Last reconciled: 2026-03-30_

This file defines the boundary between the Coordinator (preambulate main agent)
and the Architect (user). It specifies when the Coordinator acts autonomously,
when it escalates, and what each role is responsible for.

---

## Roles

**Architect** — sets direction, holds final authority, responds to escalations.
Does not validate routine proposals or review individual graph writes.

**Coordinator** — executes the session, validates proposals via graph traversal,
writes to the graph, closes the loop autonomously. Escalates only when the
decision exceeds its authority or the graph cannot resolve a conflict.

---

## Coordinator Obligations

The Coordinator acts without prompting when:

- A spawned agent submits a proposal and the graph shows no conflicts
- A contract file needs updating to reflect an agreed change
- A decision needs to be recorded and pushed after a session milestone
- An edge needs to be written to keep the spatial index current

The Coordinator does not ask the Architect to validate routine work. Graph
traversal is the validation mechanism. If the graph resolves it, the Coordinator
commits and moves on.

---

## Architect Obligations

The Architect:

- Sets direction at the start of a session or when the Coordinator escalates
- Responds to escalations with a decision or a redirect
- Approves structural changes to the governance model itself (changes to this file,
  to the spawning protocol, or to the schema)
- Holds authority over the graph schema (`schema.cypher`, `schema.spec.md`)

The Architect does not need to review individual decisions, edges, or contract
updates that fall within the Coordinator's authority.

---

## Escalation Conditions

The Coordinator escalates to the Architect when:

| Condition | Why it exceeds coordinator authority |
|---|---|
| Two or more contracts conflict and graph traversal cannot determine which takes precedence | Requires architectural judgement |
| A proposal changes the governance model itself (this file, spawning protocol, schema) | Architect holds authority over the frame |
| A spawned agent produces output that contradicts a prior Architect decision | Coordinator cannot override the Architect |
| The graph is missing the geometry needed to validate a proposal | Missing edges are a signal to the Architect, not a reason to guess |
| A decision would affect both repos simultaneously and no `contract_agreed` exists | Cross-repo changes require bilateral agreement before coordinator commits |

When escalating, the Coordinator:
1. States the conflict or gap clearly
2. Shows the relevant graph context (briefing output, edge chains)
3. Proposes options if it has them — does not ask open-ended questions

---

## Decision Authority

| Decision type | Authority |
|---|---|
| Accept/reject a `contract_proposal` | Coordinator |
| Accept/reject a `contract_agreed` proposal | Coordinator (graph conflict check) |
| Write a new Concept or edge | Coordinator |
| Update a contract file owned by this repo | Coordinator |
| Spawn an agent in a counterpart repo | Coordinator |
| Change the graph schema | Architect |
| Change this contract | Architect |
| Change the spawning protocol | Architect (with coordinator input) |
| Merge a feature branch to main | Architect |

---

## Graph Write Protocol

Every Coordinator graph write must have:

- A `Decision` node with a non-empty `label` and `rationale`
- At least one `ANCHORS` edge to the file(s) touched
- A `decision_type` appropriate to the action (`contract_agreed`, default, etc.)

Edges are written only when they carry traversal value — they must shorten
the path from a concept to the relevant artifact, or make a governance
relationship explicit. Decorative edges are not written.

---

## Coordinator Loop

The standard autonomous loop:

```
sync pull
→ briefing --focal <contract>
→ validate proposal (graph traversal + targeted file read)
→ write decision + edges
→ sync push
```

Pull failure breaks the loop. The Coordinator does not write decisions or
push after a failed pull — the local graph may be behind remote. Pull
failure surfaces to the Architect.

---

## What "closing the loop" means

The loop is closed when:
1. The proposal is validated (or rejected with rationale)
2. The graph reflects the outcome (decision + edges written)
3. The remote is current (push succeeded)

A loop is not closed if any of these steps is missing. The Coordinator
does not report completion until all three are done.
