# AssistX #148: policy-preserving Task read projection behind isolated Neo4j broker

**2026-10-11 · Draft research, stacked on PR #295 · Production admission/failover NO-GO**

## Why a second plan is necessary

[PR #295](https://github.com/scottjoyner/auto-assist/pull/295) showed actual worker↔Neo4j network separation and a read-only broker, but also produced **source-level counterexamples**: returning only Task.id/status causes the live recovery helper to miss a recovery task and causes the drain-mode helper to misclassify critical tasks.

The current `Neo4jClient.get_task` returns all Task properties. Multiple real callers in `src/assistx/api.py`, review routes, workflows and heartbeats expect more than `id` and `status`. A full replacement would be unsafe. This slice instead introduces two **explicitly distinct, source-reviewed, non-write Cypher plans** with independent response constraints:

- `get_task_read`: `Task.id` and `Task.status` only; the narrow ordinary read does **not** accidentally reveal more fields when graph reader privileges expand.
- `get_task_claim_guard`: `Task.id`, `Task.status`, `Task.payload`, `Task.payload_json`, `Task.queue_class`, and `Task.required_capabilities` only. All six keys must appear in the response (null permitted where appropriate). Missing keys, unapproved extra fields, invalid types or unrestricted list size are **rejected rather than defaulted to ordinary work**.

The `payload` field is important: `_is_recovery_task` checks `task.get("payload") or task.get("payload_json")`. A runbook can exist only in `payload`, even with no `required_capabilities`, so omitting `payload` is an authorization regression. That case is now explicitly tested.

## Actual source policy acceptance

`tests/test_trace_task_policy_projection_parity.py` extracts the existing source functions `_is_recovery_task` and `_is_claim_allowed_for_workflow_control` from `src/assistx/api.py`. The tests prove that a **complete approved claim projection** preserves the relevant decisions for representative fixtures: recovery capability, `payload_json` runbook, **payload-only** runbook, critical task acceptance during drain, and interactive task denial during drain.

Truncated or malformed authorization-sensitive fields are rejected in the broker decoder, instead of being silently converted to a permissive or incorrectly restricted task. This is **not** a proof of parity for all task properties, heartbeat/completion/review consumers, arbitrary legacy task encodings or downstream work execution.

## Physical test (disposable x1-370 only)

Manual opt-in:
```bash
ASSISTX_DISPOSABLE_GRAPH_BROKER=1 python3 tests/probe_trace_isolated_task_broker.py
```

**PASS, 57.53 seconds, exit code 0.** A fresh, no-port, no-host-mount Neo4j 5.26 Enterprise instance, restricted broker role and two internal Docker bridge networks were created temporarily. Three synthetic Tasks test ordinary minimal read, critical queue, recovery capability and a fourth **payload-only recovery** case. A worker restricted to the worker bridge retrieved each through the broker and could not reach the literal Neo4j Bolt/HTTP IP. The graph credential's read was accepted and **CREATE was denied by Neo4j RBAC**. Existing live AssistX and Neo4j containers, Tailnet services, credentials and storage were untouched; scoped test resources were removed.

**Source/tests:** `tests/trace_disposable_task_broker.py`, `tests/trace_task_read_broker_client_research.py`, `tests/trace_disposable_task_worker.py`, `tests/probe_trace_isolated_task_broker.py`, `tests/test_trace_task_policy_projection_parity.py`. [Structured evidence](../research/evidence/assistx-task-policy-projection-parity-20261011.json).

## Still-required production gates

1. **Full Task consumer mapping**: enumerate field dependencies across claim, recovery, review, heartbeat, completion, workflow and admin routes. The new six-field plan only addresses the two cited claim/recovery helpers.
2. **Identity and operation custody**: replace fixture worker bearer authentication with trusted verified ingress, bind each request to stable principal+idempotency ID, and require independently scoped Raft CAS before actual Neo4j execution. The prototype broker still does **not** require a Raft permit.
3. **Transaction closure**: integrate the real three-host Raft/Neo4j witness from #260 with this isolated broker, generation-aware independent signer custody and replay-safe closure. Successful HTTP read or result receipt is not independent transaction closure.
4. **Credential and network migration**: move legitimate live API/worker graph uses through a broker with least-privilege server role. Only *after parity* remove direct credentials, shared Docker graph network routes, wildcard host ports 7688/7475 and alternate Tailnet access. The current production bypass from #294 remains.
5. **Operational acceptance**: prove independent OS principals, trusted proxy identity, request bursts and 1/3/5/10 client fairness, failover/restarts, storage quota/restore safety and rollback.

**Disposition:** bounded Task claim-policy read semantics and physical network/RBAC feasibility PASS; claim endpoint substitution and production graph-effect fencing **NO-GO**. Keep #148 open and this PR draft.
