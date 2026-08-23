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

### tests/test_migration_api.py (13)
Symptoms: 404 "Task not found" after dispatch flows, KeyError 'queue_class'.
Hypothesis: dispatch/reassign endpoints now require executor-token auth
(`executor token must contain three segments`) and the seeded_neo4j fixtures
predate the runtime-projection lease response shape.
Action: update fixtures to mint valid executor tokens; re-check queue_class
against current `/api/dispatches` payload.

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
