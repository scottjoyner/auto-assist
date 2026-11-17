# Auto-Assist legacy worker — recovery claim fail-closed patch

## Scope and source identity
2026-11-16, isolated source from draft Auto-Assist PR #482 exact head
`07c95e123dd2f4bd0dac69bfea13ca8dd6df0a4e`.
This patch is a **review dependency** for the existing PR, not a live
worker deployment and not machine-principal admission.

## Independently identified omission
The worker excludes tasks with literal `required_capabilities=["recovery"]`
from its READY query/selection. But a READY task can carry an embedded
`payload.runbook` or `payload_json.runbook` with no recovery-capability
tag. A post-claim authoritative Task can also differ from its READY entry
and contain a newly exposed runbook. Source `_claim_and_run` could
execute a `task_type="script"` recovery runbook if the separate recovery
feature flag was enabled, despite the legacy worker lacking a token-
fenced recovery principal. No actual exploitation or live Task claim
is asserted by this code audit.

## Fail-closed changes
- Shared `_legacy_worker_recovery_denied()` detects script/recovery
  type, case-insensitive recovery tags, dict or JSON runbook envelopes,
  malformed/oversized/duplicate JSON and suspicious payload types.
- `_poll_once` refuses such READY entries before claiming anything.
- `_claim_and_run` also rejects the direct READY task before HTTP,
  removes recovery from any advertised claim capabilities, and
  re-checks the authoritative server CLAIMED task *before* heartbeat,
  model work, a runbook or completion. The server's authoritative
  CLAIMED task must also retain the requesting node as target (or remain
  untargeted) and require only this node's current non-recovery
  capabilities; claim fencing alone is not audience authorization.
- If a server unexpectedly grants a claim whose returned payload is a
  recovery runbook, the worker returns false without execution or
  completion. The server lease must expire; the worker does **not**
  assert it has released or completed the claim.
- `_legacy_worker_llm_dispatch_eligible()` now requires the current `llm`
  capability, a configured local endpoint, and an LLM or legacy plain task
  before READY selection, direct claim, and acceptance of a CLAIMED task.
  Unsupported GPU, vision and script task types are not claimed even if
  `required_capabilities` is omitted. This is local denial only; it does
  not grant signed model residency or Task authorization.
- Exported `execute_task()` retains its separate typed runbook path for
  independently authorized callers; this patch fences only the legacy
  polling and claim path that lacks per-node recovery authorization.

## Regression / delivery acceptance
```sh
python3 -B -m pytest -q -p no:cacheprovider \
 tests/test_fleet_node_recovery_claim_guard.py \
 tests/test_fleet_node_poll_contract.py \
 tests/test_fleet_node_recovery.py \
 tests/test_fleet_claim_response_shape.py \
 tests/test_fleet_node_shell_gate.py \
 tests/test_fleet_executor_concurrency.py \
 tests/test_fleet_node_lmstudio_auth.py \
 tests/test_fleet_node_kv_cache.py
```
The expanded offline suite returned 117 PASS. It uses synthetic HTTP,
never a real router, API, Task claim, Neo4j write or inference endpoint.

**Still HOLD:** machine-principal node registration and secrets custody,
task eligibility on the server, signed physical capacity, transactional
claims/leases, owner-led one-node staging, CI exact head and review.
Do not deploy the legacy Basic-auth worker merely because these tests pass.
