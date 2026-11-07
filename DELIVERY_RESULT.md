# DELIVERY_RESULT — Fleet Node Admission & Auth (FLEET-01, 2026-11-04)

STATUS: PASS
TESTS: PYTHONPATH=src python3 -m pytest tests/test_fleet_node_auth.py tests/test_fleet_node_api.py -q => 12 passed, 1 skipped (api-level module skipped: importorskip — this isolated worktree has no `langgraph`; runs unchanged under full requirements). PYTHONPATH=src python3 -m pytest tests/test_node_identity.py tests/test_fleet_node_recovery.py tests/test_fleet_node_shell_gate.py -q => 7 passed. PYTHONPATH=src python3 -m pytest tests/test_fleet_node_lmstudio_auth.py tests/test_fleet_node_lmstudio_auth_loopback.py tests/test_fleet_node_kv_cache.py -q => 29 passed. python3 -m py_compile src/assistx/api.py src/assistx/fleet_node_agent.py src/assistx/fleet_node_auth.py => OK (full assistx.api import not possible here: `langgraph` not installed; no dependencies were installed).

PASS denotes only scoped research validation in this worktree. It is NOT production approval; production rollout remains HOLD, the signed projection remains expired, and zero slots remain approved.

## Problem

Fleet nodes (xwing, Deathstar, Destroyer, Lenovo, OptiPlex) present per-node identity credentials, but the deployed task-list/claim path only accepted the general Basic credential (`BASIC_AUTH_USER`/`BASIC_AUTH_PASS`), so nodes received HTTP 401. Separately, nodes that are operationally healthy are not admitted anywhere (signed runtime projection expired, zero approved slots) — reproduced with synthetic fixtures and left fail-closed (not "fixed").

## Smallest safe compatibility/identity boundary

- New module `src/assistx/fleet_node_auth.py`: `evaluate_fleet_node_identity()` verifies `X-Fleet-Node-Id` + `X-Fleet-Node-Token` against `ASSISTX_FLEET_NODE_TOKENS` (constant-time compare via existing `node_identity.verify_node_token`). No headers -> `None` (caller may fall back to legacy Basic). Any node credential header present but missing/unknown/mismatched/registry-unparseable -> `FleetNodeAuthHold` -> HTTP 403, never falls back to weaker auth. `fleet_node_binding_error()` binds the verified identity to the target agent_id.
- `src/assistx/api.py`: new `fleet_node_or_basic_auth` dependency (same 401 contract for unauthenticated callers; 403 for bad node credentials; legacy Basic retained unchanged). Wired into exactly the fleet-node-agent lifecycle endpoints: `GET /api/agent/tasks`, `POST /api/tasks/{id}/claim`, `/heartbeat`, `/complete`, `/checkpoint`. When node-authenticated, the target `agent_id` (query or body) must equal the verified node id, else 403 — checked BEFORE any graph access. Recovery-task verification (`_verify_recovery_node_identity`) is unchanged and still applies. All other routes keep `Depends(auth)`.
- `src/assistx/fleet_node_agent.py`: the agent sends `X-Fleet-Node-Id`/`X-Fleet-Node-Token` only for AssistX task-list/claim/heartbeat/complete (all opt-in via `FLEET_NODE_TOKEN`+`FLEET_NODE_ID`; missing either -> no headers, unchanged behavior). Header is never attached to LM Studio or other hosts. The task-list poll now also passes `agent_id=<node>`, which includes untargeted READY tasks plus tasks targeted at this node (matches claim semantics).

## Files changed

- `src/assistx/fleet_node_auth.py` (new)
- `src/assistx/api.py`
- `src/assistx/fleet_node_agent.py`
- `tests/test_fleet_node_auth.py` (new, synthetic fixtures)
- `tests/test_fleet_node_api.py` (new, API-level; importorskip when app deps absent)
- `DELIVERY_RESULT.md` (this file)

## Evidence

- Unit tests pin: no headers -> defer; partial headers -> 403; unregistered node -> 403; wrong token -> 403; registry missing/malformed -> 403; registered node -> accepted; binding error only for node-authed callers; binding mismatch -> rejected before graph access; node agent attaches headers only with both env vars, never without node id.
- Fail-closed admission preserved: `test_work_supply_zero_slots_when_projection_unavailable` (expired projection -> `total_slots=0, free_slots=0, allow_background=False`) and `test_projection_marks_unadmitted_node_blocked` (synthetic tailnet `observer_only` node -> `lane="blocked"`, `can_use_free_api=False`).
- Existing recovery/LM-Studio-auth suites still pass (29 tests).

## Risk

- Additive path: Basic-auth behavior is byte-identical; only five endpoints branch. Blast radius limited to fleet-node lifecycle routes.
- Node tokens presented are all-or-nothing (both headers required); mismatched/unknown -> 403, never a silent Basic fallback.
- No gates relaxed: drain mode, claim limits, recovery identity checks, projection checks, benchmark quorum — all untouched.

## Remaining gates / unresolved

- API-level HTTP tests could not be executed here (no `langgraph`; dependencies NOT installed by policy). They run unchanged in CI/full requirements; if they fail there, revert this patch rather than weaken auth.
- Production rollout remains HOLD: signed projection expired, zero approved slots, node admission mismatch deliberately NOT remediated (no projection/admission changes; no node registration; no credentials touched; no services restarted).
- Nodes must be redeployed/configured with `FLEET_NODE_ID` (or hostname) + `FLEET_NODE_TOKEN` matching `ASSISTX_FLEET_NODE_TOKENS` before live use; `deploy/canary.env.example` / `.env.example` already document this convention — no change made.
- Router-side rollout (node-report capability advertisement) not modified; out of scope.

## Proposed PR description

Title: fleet-node-agent: authenticate task list/claim with per-node identity (fail-closed)

Allow the fleet node agent to authenticate `GET /api/agent/tasks`, `POST /api/tasks/{id}/claim|heartbeat|complete|checkpoint` with its registered per-node identity (`X-Fleet-Node-Id` + `X-Fleet-Node-Token`, verified against `ASSISTX_FLEET_NODE_TOKENS`, constant-time compare). Node-authenticated requests may act only as their own identity; mismatch -> 403 before graph access. Requests without node headers behave exactly as before (legacy general Basic credential). Partial/invalid node credentials -> 403, never a fallback. Projection/admission untouched; production rollout remains HOLD.

## Validation boundary

Not run: any live/production probe, Neo4j container suites (memory/weight), full `assistx.api` import (deps absent). Local evidence = focused unit tests + py_compile + adjacent existing focused suites. Production admission and node onboarding require operator approval and separate validation.

## Independent API test verification — November 4, 2026, 04:13 EST

The post-delivery review corrected a test-only import (`fastapi.testclient` was referenced before its module was imported) and independently ran the previously skipped API-level tests against a pre-existing Auto-Assist application Docker image with `langgraph` installed.

- Container execution: `--pull never --network none --read-only --user 1000:1000 --memory 2g --cpus 2`; no access to host services, container secrets, or network.
- The research worktree and the necessary pytest packages were mounted read-only; only synthetic test fixtures were used.
- `python -m pytest -q -p no:cacheprovider tests/test_fleet_node_api.py tests/test_fleet_node_auth.py` (application-image Python, `PYTHONPATH=/work/src:/opt/pytest`) => **19 passed, 0 skipped, 0 failed**.
- Independent isolated-host suite of eight targeted modules => **48 passed, 1 skipped** (the API module skips only on the lightweight host; it executed and passed in the container above).
- Five local dispatcher gate tests => **5 passed** (cooldown, two separated resource checks, active-run exclusion, unsafe-resource reset, dispatch fail-closed).
- This is still **research-only PASS**, not hosted exact-head CI, merge, production credential registration, or release authorization. No live node credentials or production deployment were changed.

## Continuation — November 6, 2026, 22:30 EST (Fleet Commander)

STATUS: PASS

TESTS: `python3 -m pytest -q -p no:cacheprovider tests/test_fleet_node_api.py tests/test_fleet_node_auth.py` on lightweight host: 19 passed, 1 skipped; same suite in existing isolated application image `auto-assist-wt-ab3bb64-api:latest`: 33 passed, 0 skipped, 0 failed.

- Added seven synthetic API-level negative tests in `tests/test_fleet_node_api.py`: 3 invalid/partial node-header cases must return 403 despite independently valid legacy operator Basic credentials; 4 cross-node mutation cases (claim, heartbeat, complete, checkpoint) must reject before Neo4j initialization.
- Docker verification used `--pull never --network none --read-only --user 1000:1000 --memory 2g --cpus 2 --pids-limit 128`, read-only source/dependencies, ephemeral `/tmp` for the synthetic outbox, and no external credentials or services.
- `git diff --check` passed. Local source worktree retains the preexisting untracked `FLEET_DELIVERY_BRIEF.md` (intentionally untouched).
- Git still reports the malformed shared reference `refs/heads/fix/fleet-node-worker-poll-contract-20261102`; do not prune, delete or force-repair it. Promotion requires independently reviewed repo custody and hosted exact-head CI.
- Existing production HOLD is unchanged: expired projection, zero approved execution slots, incomplete physical node registration/authentication and no controlled rollout acceptance.

## Read-only shared Git ref recovery evidence — November 6, 2026

- The shared loose ref `refs/heads/fix/fleet-node-worker-poll-contract-20261102` is a regular zero-byte file (mtime 2026-11-02 18:25:36 EST); its associated loose reflog is also zero bytes; no matching packed-ref entry.
- In this local shared object store `git cat-file -t 96d3943` reports `Not a valid object name`; do not use that prior short SHA as a recovery source.
- Read-only `GIT_TERMINAL_PROMPT=0 git ls-remote origin` confirms both `refs/heads/fix/fleet-node-worker-poll-contract-20261102` and `refs/pull/482/head` resolve to remote SHA `07c95e123dd2f4bd0dac69bfea13ca8dd6df0a4e`. The GitHub connector independently confirms PR #482 remains open.
- No Git ref deletion, restoration, fetch, prune, garbage collection or force update was attempted. Quarantine/snapshot, recover and verify remote objects in a separate clean repository, then separately approve a shared-ref repair with rollback before any local normalization. Production HOLD unchanged.
