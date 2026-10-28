# Structured Result Contract

Use one result envelope across tools. Field names may evolve, but success/failure semantics must remain consistent.

## Success example

```json
{
  "ok": true,
  "code": "OK",
  "operation_id": "op:8f2c",
  "tool": "catia_pocket",
  "message": "Pocket created and verified",
  "data": {
    "feature_handle": "h:42"
  },
  "effects": {
    "volume_before_mm3": 240000.0,
    "volume_after_mm3": 228219.0,
    "volume_delta_mm3": -11781.0,
    "topology_generation_before": 17,
    "topology_generation_after": 18
  },
  "diagnostics": [],
  "warnings": [],
  "recovery": {
    "operation_group_id": "grp:2a1d",
    "rollback_supported": true
  }
}
```

## Failure example

```json
{
  "ok": false,
  "code": "STALE_TOPOLOGY_TOKEN",
  "operation_id": "op:a93b",
  "tool": "catia_create_edge_fillet",
  "message": "Topology reference is stale for the current body generation",
  "data": {
    "current_topology_generation": 18
  },
  "effects": {},
  "diagnostics": [],
  "warnings": [],
  "recovery": {
    "recoverable": true,
    "next_actions": ["list_edges"]
  }
}
```

## Required semantics

- `ok`: authoritative success/failure boolean.
- `code`: stable machine-readable outcome code.
- `operation_id`: correlation identifier.
- `tool`: logical tool name.
- `message`: concise human-readable summary; never the only error channel.
- `data`: primary tool output.
- `effects`: measured model changes and generation changes.
- `diagnostics`: CATIA/CAA/update diagnostic records.
- `warnings`: non-fatal limitations.
- `recovery`: recovery availability and next safe action where appropriate.

### What this server actually emits in `recovery`

The examples above come from the upstream full bridge and describe fields this
lite fork does not implement (`operation_group_id`, `recoverable`). The real block
is built in `catia_mcp/results.py::operation_result`, from FACTS collected by the
tool that failed:

| field | meaning |
|---|---|
| `rolled_back` | `true` the feature this call created was removed again, `false` it could not be removed, `null` no feature was ever created |
| `automatic_retry_allowed` | always `false`: retrying automatically is the caller's decision |
| `can_request_rollback` | always `false`: no tool takes a rollback argument (the name says what the constant means - `rollback_supported` was removed in 0.3.35 because a false there read as "this failure could not be rolled back" beside `rolled_back: true`) |
| `rollback_note` | present only when `rolled_back` is not null: the sentence the constants above stand in for |
| `next_actions` | what is left to do, written by the raise site when it knows better than the default |

Read-only tools (`catia_get_*`, `catia_list_*`, `catia_measure_*`,
`catia_diagnose`, `catia_export`, plus named exceptions - see `is_read_only`)
emit **no** `recovery` block at all: a lookup that wrote nothing has nothing to
recover, and "inspect the model before retrying" on such a failure is advice to
check a model nothing touched.

When such a tool DOES know what the caller should do next, the actions are still
published - in `data.next_actions`, with `data.next_actions_note` explaining why
they are not in `recovery`. They are about the ARGUMENT this call used ("pass the
full path of the copy you mean"), not about a model state to repair, and
`DOCUMENT_NAME_AMBIGUOUS` is raised almost only by read-only tools, so suppressing
the block was silently dropping its advice (reported 2026-10-28, P2).

`tensor_reason_code` is one of `nested_in_assembly` (an open assembly holds the
part, named in the sentence), `not_activated` (nothing holds it: a focus refusal,
fixed with `catia_activate_document`) or `unreadable` (CATIA raised on the query).
It used to be the single constant `no_own_window`, which asserted "component of an
open assembly" about parts no assembly referenced.

## Error codes

Use stable codes as applicable:

- `OK`
- `INVALID_ARGUMENT`
- `NOT_CONNECTED`
- `NO_ACTIVE_DOCUMENT`
- `WRONG_DOCUMENT_TYPE`
- `OBJECT_NOT_FOUND`
- `STALE_HANDLE`
- `STALE_TOPOLOGY_TOKEN`
- `UPDATE_FAILED`
- `FEATURE_NO_EFFECT`
- `WRONG_GEOMETRY_EFFECT`
- `COM_ERROR`
- `COM_TIMEOUT`
- `SESSION_BLOCKED`
- `SESSION_STATE_UNKNOWN`
- `MODAL_DIALOG_BLOCKED`
- `MEASUREMENT_FAILED`
- `ROLLBACK_FAILED`
- `UNSUPPORTED_CAPABILITY`
- `CAA_BRIDGE_UNAVAILABLE`
- `BRIDGE_PROTOCOL_ERROR`
- `INTERNAL_ERROR`

## Propagation rules

1. Never convert lower-layer `ok=false` into outer-layer success.
2. Preserve the most specific available error code.
3. If bridge JSON cannot be decoded, return `BRIDGE_PROTOCOL_ERROR`; do not infer success from partial text.
4. Sanitize/truncate raw diagnostic payloads before logging or returning them when needed.
5. Do not use a normal transport response as evidence that the CAD operation succeeded.

## Effects contract

Material-changing tools should return measurable effects where available:

- volume before/after/delta;
- mass/area/COG changes when relevant;
- bbox before/after when relevant;
- update status;
- topology/model generation before/after;
- target handles/tokens consumed.

If a required effect cannot be measured, return an explicit warning or inconclusive/error classification rather than fabricating a value.

## Tool description contract

Tool documentation must state:

- supported semantics;
- units;
- input/reference lifecycle;
- mutation side effects;
- topology-generation behavior;
- relevant error codes;
- known unsupported variants.
