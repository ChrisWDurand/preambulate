# Cross-File Call Resolution — Interface Specification

_Owner: preambulate/infer.py_
_Depends on: symbol extraction (Task 2, complete)_
_Implements: Task 4_
_Version: 0.1.0_
_Status: DRAFT — pending review before implementation_

---

## Purpose

Task 2 (complete) extracts symbols and within-file call edges. A caller in `sync.py`
that calls a function defined in `graph.py` produces no edge today — the call crosses
a module boundary and falls outside the current scope.

This spec defines how cross-file call edges are inferred and recorded so that a
function call chain can be traversed up through the decisions that shaped each callee,
regardless of which file it lives in.

---

## What Changes

A single new phase is added to `infer_file()`:

| Phase | Existing | Change |
|---|---|---|
| 1 — import-level DERIVES_FROM | yes | extended to capture imported names (see below) |
| 2 — symbol extraction + GOVERNS | yes | unchanged |
| 3 — within-file call resolution | yes | unchanged |
| **4 — cross-file call resolution** | no | **new** |

---

## Symbol Index

Cross-file resolution requires a lookup: "given symbol name `X`, which Artifact
node is the canonical definition of `X`?"

This index is built in memory during `infer_all()` and is not materialized in the
graph. It is rebuilt on every full scan. The graph's `Artifact` nodes are the
source of truth; the index is a query cache.

**Structure:**
```
symbol_index: dict[str, str]
  key   — base symbol name (e.g. "open_graph", "GraphConnection")
  value — artifact path of the defining file (e.g. "preambulate/graph.py")
```

Populated by: querying all `Artifact` nodes where `path` contains `::` (symbol
artifacts), extracting the base name from the `::` suffix and the file path from
the prefix.

**Collision policy:** If two files define a symbol with the same base name, the
index keeps the first entry encountered (alphabetical file order during infer_all).
This is an approximation — precision improves when call sites are qualified
(e.g. `graph.open_graph()`), which Phase 4 handles separately (see below).

---

## Extended Import Extraction

Phase 1 currently records: `file_A -[DERIVES_FROM]-> file_B` when A imports B.

Phase 1 is extended to also record **which names** were imported from each module:

```
imported_names: dict[Path, set[str]]
  key   — resolved file path of the imported module
  value — set of imported names from that module
           ("*" for star imports — treated as unresolvable)
```

Captured from:
- `from module import foo, bar` → `{module_path: {"foo", "bar"}}`
- `from module import *` → `{module_path: {"*"}}`
- `import module` → names used as `module.foo` resolved in Phase 4

This map is local to `infer_file()` and passed into Phase 4. It is not stored in
the graph directly.

---

## Phase 4 — Cross-File Call Resolution

**Input:**
- `tree` — AST of the current file
- `imported_names` — map from module path → set of imported names (from Phase 1)
- `symbol_index` — global index of base name → defining file path
- `rel_src` — relative path of the current file being processed

**Algorithm:**

For each function or method in the current file:
1. Walk all `ast.Call` nodes within that function body.
2. For each call:
   a. **Qualified call** (`module.foo()`): resolve `module` to a file path via
      `imported_names`. Look up `foo` in the symbol artifact for that file.
   b. **Bare call** (`foo()`): check `imported_names` for any module that
      explicitly imports `foo`. If found, look up the symbol artifact in that module.
   c. **Symbol index fallback**: if no explicit import record exists and `foo` is
      not defined locally (not in `defined_names`), look up `foo` in `symbol_index`.
      Only used when exactly one file defines a symbol by that name.
3. If a target symbol artifact is found, create:
   `caller_symbol -[DERIVES_FROM]-> callee_symbol`
   with rationale: `"Cross-file call: {caller} → {callee} ({src_file} → {tgt_file})"`

**Skip conditions:**
- Call target is in `defined_names` (handled by Phase 3)
- Module path is `"*"` (star import — unresolvable)
- Symbol index returns no match (unknown external call)
- caller_path == callee_path (self-call — already handled by Phase 3)

---

## Edge Semantics

Cross-file call edges use the same `DERIVES_FROM` relationship as within-file calls.
The rationale string distinguishes them:

| Context | Rationale prefix |
|---|---|
| Import-level (Phase 1) | `"Inferred from Python import: ..."` |
| Within-file call (Phase 3) | `"Inferred from call: ..."` |
| Cross-file call (Phase 4) | `"Cross-file call: ..."` |

No new edge type is introduced. `DERIVES_FROM` between symbol Artifacts is the
correct semantic: the calling symbol's behavior is downstream of the callee's
definition.

---

## Idempotency

Phase 4 uses `_ensure_symbol_derives_from()` — the same guard used by Phase 3.
Re-running infer on a file that already has cross-file edges is a no-op.

---

## CLI / Hook Behavior

No new CLI command. Phase 4 runs automatically as part of `infer_file()`.

In hook mode (PostToolUse), Phase 4 runs only for the edited file. The
`symbol_index` is built from whatever the graph already contains at the time of
the hook invocation — it does not re-scan other files. This means cross-file
edges accumulate over time as files are edited, rather than requiring a full scan.

Full scan (`preambulate infer` or `infer_all()`) builds the complete index before
processing and produces a fully connected symbol graph in one pass.

---

## Limitations

1. **Name shadowing**: a local variable that shadows an imported name produces a
   spurious cross-file edge. Acceptable approximation in v1.
2. **Dynamic calls** (`getattr(obj, name)()`): not resolved. Omitted silently.
3. **Aliased imports** (`from module import foo as bar`): `bar` is not currently
   tracked. Phase 1 extension must capture the alias mapping. Deferred to v1.1.
4. **Star imports**: unresolvable. Any call that could only be resolved through a
   star import is silently skipped.
5. **Same-name symbols in multiple files**: index keeps first entry. Low-precision
   when common names (e.g. `main`, `run`) exist across many modules.

---

## Acceptance Criteria

- [ ] `infer_file()` on `preambulate/capture.py` produces a DERIVES_FROM edge from
      `capture.py::_record_session` to `graph.py::open_graph`
- [ ] Re-running infer on the same file produces no duplicate edges
- [ ] Hook mode (single-file) accumulates edges correctly without full re-scan
- [ ] `infer_all()` on the preambulate package produces a connected symbol graph
      traversable from any entry-point function down to `graph.py::open_graph`

---

## Schema Impact

None. No new node types or relationship types. `DERIVES_FROM` between `Artifact`
nodes (kind=function/method/class) already exists in the schema.

---

## Files Affected

| File | Change |
|---|---|
| `preambulate/infer.py` | Add `imported_names` capture to Phase 1; add Phase 4 |
| `preambulate/infer.py` | `infer_all()` builds and passes `symbol_index` |
| `preambulate/infer.py` | `main()` passes `symbol_index` in hook mode (from graph query) |
