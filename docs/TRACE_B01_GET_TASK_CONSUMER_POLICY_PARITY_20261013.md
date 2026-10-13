# B01 get_task substitution safety — October 13, 2026

**Research-only / no production changes / B01 NO-GO.** Stacked on draft
#347 at `f01b1d58f88720585817f5a65007dff847e67314`.
This is a source-pinned *synthetic* counterexample to treating the
disposable broker's `get_task_read` result as a generic
`Neo4jClient.get_task` replacement. No real Task IDs, graph queries,
Docker mutations, network changes or credentials are used.

## Current contracts in checked-in code

- `Neo4jClient.get_task(task_id)` executes `RETURN t` and converts the
  **whole Task node** into a dict. It may contain policy, payload and
  sensitive values. It is not safe to expose this result wholesale.
- Disposable `TaskReadBrokerClient.get_task` returns only `id,status`
  and deliberately accepts only `fixture-task-*` IDs. This is a
  synthetic subset, **not full Task parity or real-ID compatibility**.
- The separate `get_task_claim_guard` fixture returns six fields:
  `id,status,payload,payload_json,queue_class,required_capabilities`.
  It has a narrow purpose and also lacks production identity and
  authority integration.
- Real API `api_claim_task`, `api_heartbeat_task`, and
  `api_complete_task` call legacy `get_task`, and `api_get_task`
  still uses a direct `_session` to read Task plus Transcription and
  AgentRun relationships. None of these routes has been migrated.

## Reproduced failure — exact pure API helpers, synthetic records

The opt-in-independent `trace_b01_task_consumer_parity.py` parses
`src/assistx/api.py` with AST and compiles **only** the three
source-pinned helpers needed for recovery and drain policy:
`_json_dict`, `_is_recovery_task`,
`_is_claim_allowed_for_workflow_control`. No FastAPI or Neo4j app
initialization occurs. A synthetic READY Task has a `recovery`
capability, a runbook, and critical queue class.

| API policy decision | Full Task | Two-field broker | Six-field claim-guard |
| --- | --- | --- | --- |
| Recovery identity path required | true | **false** | true |
| Critical Task allowed in drain | true | **false** | true |

These are *function-level* classifications, not a complete HTTP auth
or graph-write exploit. The wrong recovery classification means a naive
two-field substitution could skip the route's existing
`_verify_recovery_node_identity` check. **Do not substitute it.**
## Smallest acceptance before replacement

1. Do not rename a status-only broker plan to a generic `get_task`.
   Define a distinct and explicitly approved policy-view plan that
   performs recovery/queue classification with bounded, typed fields
   and minimal disclosure. Audit and classify raw runbook/payload data.
2. Bind the authenticated API/worker principal to the authorized task
   and command; reject caller-provided scope, raw Cypher and alternate
   graph URLs. Replace fixture-only Task ID validation through a
   separately reviewed bounded real-ID contract; no permissive regex
   change without production ID census and ownership authority.
3. Preserve exact recovery identity requirements and drain/queue
   policy under synthetic full records, missing/null/malformed values,
   resource-denial cases, and live-like fixture transport.
4. A pure read-only claim guard **is not a fenced claim**. The subsequent
   mutation must atomically reject stale policy, stale leader term and
   replay at the resource boundary, or be serialized by an independently
   proven authority. No optimistic time-of-check/time-of-use shortcut.
5. Inventory/replace the direct `_session` routes, write paths and
   asynchronous consumers **before** severing graph access. PR #301's
   AST numbers (72 methods, 149 candidate calls, 194 direct-session
   indications, 57 files) are source indications, not production call
   traffic or authorization.
6. Run a disposable full service-image acceptance against a fixed
   broker, with negative direct Bolt/HTTP via Docker DNS, literal IP,
   wildcard host port and Tailnet routes and independent ingress
   provenance. Independently review rollback and credential custody.

**No claim that existing source presently bypasses recovery auth:** only
a proposed unsafe replacement was shown to misclassify a recovery Task.
Do not enable this replacement, merge, deploy, revoke live credentials,
disconnect production networks, or promote failover based on this slice.