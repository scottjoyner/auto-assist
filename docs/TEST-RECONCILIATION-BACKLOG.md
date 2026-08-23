# Test reconciliation backlog (2026-08-23)

Origin/main shipped with 27 failing unit tests caused by two overlapping
refactors (`9d8751a5` strict executor auth, `b94254df`/`c76eb740` cleanups)
that changed contracts without updating their tests. Current status: **21
failing**, all in the clusters below.

## Done
- `test_benchmark_controller` — restored `fleet_node_agent.execute_task`
  (benchmark scoring) and wired it into `_claim_and_run`.
- `test_fleet_node_shell_gate`, `test_fleet_node_kv_cache` — ported the LLM +
  KV-cache adapter path back into `execute_task`; shell/vision commands stay
  removed (tests updated to assert rejection instead of gating).
- `test_control_room::test_legacy_operator_pages_are_consolidated` — legacy
  redirect set trimmed to true duplicates only (operator pages render again).

## Remaining clusters (in suggested order)

### tests/test_migration_api.py (6 remain)
ROOT CAUSE FOUND & FIXED (2026-08-23): routers bind `_neo` from assistx.api at
import time; tests only patched assistx.api._neo, so router endpoints hit
PRODUCTION Neo4j (verified: ephemeral DB was clean while test read fleet-gen
tasks from June). Isolation fixture now patches every assistx.routers.*._neo.
Also purged 21 test ContextPackets from prod graph.

Remaining 6 encode the OLD sophia voice contract (queue_class,
routing_policy_fingerprint). Canonical route is now
voice_routes.api_legacy_sophia_voice_event (registered before api.py's
deprecated handler) returning the strict-executor envelope:
{accepted, authorization_action, review_required, audit_only,
legacy_endpoint, contract_fingerprint}. NOTE: basic-auth callers are treated
as trusted operators by _authenticate_transport, so auth_state anomalies are
overridden - decide whether that is intended before migrating assertions
(test_sophia_event_ingestion partially migrated; routing_policy_override,
phase8 x2, task_trigger_lifecycle, ticket_hierarchy follow the same pattern).

### tests/test_swarm_phase2.py (4)
Symptoms: trace linkage assertions fail around EventEnvelope links.
Note: links are now list[EventLink] per canonical contract (fixed in
auto_assign_client); swarm_core trace recording may still expect dict-form
links somewhere. Align swarm_core to the list form and assert FOR_TASK links.

### tests/test_fleet_node_recovery.py (3)
Recovery runbook executor signatures drifted from `_claim_and_run` refactor.
Likely small: match RecoveryRunbookExecutor.execute_task_payload kwargs.

### tests/test_improvement_runtime.py (2)
Improvement-cycle proposal state machine drift; inspect against
`/api/fleet/improvement-cycle` handler.

## Ground rules
- Fix implementation to match the *canonical* contract when the intent is
  clear (benchmark outcomes, EventEnvelope, KV-cache allowlist).
- Update tests when they encode deliberately removed behavior (generic shell).
- Never make tests pass by weakening security assertions.
