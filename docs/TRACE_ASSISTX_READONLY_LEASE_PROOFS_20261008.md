# AssistX claimed-task lease proofs — read-only / disabled-by-default gate

**Decision date:** 2026-10-08 America/New_York.
**Decision purpose:** Bind node execution proofs to the *existing live Neo4j Task claim* rather than the earlier offline synthetic shadow fixture.
**Decided by:** User requested continuation; engineering scope is a review-only read path, with no task creation, command execution, service restart or live promotion.
**Decision:** Add disabled-by-default read-only lease and status-proof routes to AssistX. Require authenticated AssistX user and registered fleet-node token, current claimed node, exact claim ID, claim generation, typed trace-probe capability and a valid lease. The signed lease is insufficient on its own: nodes must separately obtain a newly signed 1.5-second status response bound to a fresh random node challenge.
**Reopen:** Only after a real Neo4j-owned claim exists, production API import/runtime dependencies pass, dedicated signing key is provisioned and secured, node verifies a live challenge against this API, and cancellation/re-claim negative drills pass may an actual AssistX-issued synthetic no-op be authorized. Any non-synthetic typed command requires further permission.

## Prediction and experiment prospectus

**Hypothesis:** A read-only signing service using an existing Neo4j claim can reject wrong-node, wrong-claim-generation, revoked/cancelled and expired cases before signing. A short-lived signed challenge from a *second* graph lookup reduces acceptance of stale previously signed claims. The shadow remote verifier should import and exercise crypto checks on Linux/Python 3.12 and macOS/Python 3.9.

**Prospective repeatable acceptance:** Status `CLAIMED` or `RUNNING`; `task_type=trace_probe`; `payload_json={"command_id":"probe.noop.v1"}`; `claimed_by` and `target_agent_id` both equal the authenticated node; `required_capabilities` includes `trace-probe`; `claim_id` and `execution_attempt` match; `lease_expires_at_ts` must have >10 seconds remaining. The issuer signs a 10-second Ed25519 lease and, on a second current read, a <=1500 ms signed status linked to that lease digest and a fresh 32-byte random challenge. A cancelled/re-claimed task must fail the second read. Neither proof alone may authorize execution.

The prediction above was documented during engineering after the first exploratory tests; **it is not a pre-registration of the tests already run**. Use this document as the prospective plan for independently repeated acceptance.

## Implementation and authority scope

- `src/assistx/trace_claim_lease.py`: pure claim fields validator, Ed25519 signed lease and current-status proof, strict issuer/schema/capability and clock checking, challenge-digest linkage, key ownership checks.
- `src/assistx/trace_claim_lease_api.py`: endpoints `POST /api/fleet/trace-execution/claim-lease-proof` and `POST /api/fleet/trace-execution/claim-current-status` with the existing authenticated user and registered `x-fleet-node-token`. Both return verify-only results, never mutate a claim.
- `src/assistx/api.py`: registers the additive routes but **they return 503** unless `ASSISTX_TRACE_LEASE_ISSUER_ENABLED=true`; enforces node token and explicit target for trace-probe claim operations; requires node token and claim ID for trace-probe heartbeats/completions. Other task types retain their existing boundaries.
- `tests/test_trace_claim_lease.py`: offline TestClient mock-Neo4j API tests, wrong-node/expiry/claim generation, payload restrictions, invalid keys, disabled-by-default checks, fake cancellation after lease signature and challenge replay negative tests.
- `scripts/trace_claim_lease_platform_smoke.py`: run *only in-memory fixtures* on both shadow nodes; no actual graph read, signer provisioning, journal write or command execution.
- A distinct dedicated issuer signer must eventually be configured via `ASSISTX_TRACE_LEASE_SIGNING_KEY_FILE`; **do not reuse the offline shadow signer as an AssistX authority key**. No issuer signing secret was generated or staged in this slice.

**Critical limitations:** The second proof reflects graph state at its issuance time; a cancellation immediately after signing can leave up to **1.5 seconds** of freshness window (plus clock skew). This is NOT instantaneous distributed revocation nor hard real-command fencing. Require an explicit monotonic lease/revocation generation, connected fail-closed node status check, and pre-execution freshness evidence before a real typed command. The `trace_claim_lease` read path does not own physical allocation/claims. Auto-router remains strict-offline inference forwarding only.

## Observations — October 8, 2026

- Claim-proof issuer and second current-status API were tested through a mock-Neo4j TestClient; no Neo4j rows were created/modified. 22 dedicated focused tests passed.
- The prior complete trace-executor/custody/recovery focused regression slice plus this module: **87 passed**; one known TestClient dependency deprecation warning, no failures.
- The isolated shadow code on **xwing** (Linux/Python 3.12) and **scotts-macbook-air** (macOS/Python 3.9) imported the pure lease verifier and validated in-memory fixtures; both returned `ok=true`, `executed_commands=0`, `assistx_claim_verified=false`, and denied missing or wrong-challenge status proof.
- The code was staged under existing **shadow release directories** only; no agent auto-start/system service, issuer private key, token secret, live API deployment or OpenTunnel installation.
- Existing node audit journals and encrypted NAS history were not reset, overwritten or purged.

## Reproducibility

```bash
cd /home/scott/git/wt-assistx-trace-execution-20261007
PYTHONPATH=src python3 -m pytest -q \
    tests/test_trace_claim_lease.py \
    tests/test_trace_shadow_grant.py \
    tests/test_trace_execution_shadow_backup.py \
    tests/test_trace_execution_adapter.py \
    tests/test_trace_execution_shadow_paths.py \
    tests/test_fleet_node_shell_gate.py \
    tests/test_fleet_node_recovery.py \
    tests/test_safe_fleet_executor.py \
    tests/test_fleet_executor_concurrency.py
# These standalone commands use an ephemeral local test signer and cannot run work:
# (xwing and MacBook Air scripts are staged in their existing shadow dirs.)
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes xwing \
  'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/scott/.local/opt/assistx-trace-shadow/v1/src python3 /home/scott/.local/opt/assistx-trace-shadow/v1/scripts/trace_claim_lease_platform_smoke.py --node-id xwing'
```

## Remaining acceptance gates

1. **Production API dependency baseline:** A separate pre-existing local `langgraph` import issue prevents importing the monolithic `api.py` in this scratch Python environment. The isolated router was tested with FastAPI; an actual production API startup/deployment was NOT verified.
2. **Actual AssistX claim:** No live Neo4j task claim was created or read by the tests. Operator needs a separate sandbox real claim owned by AssistX to prove read-only issuance, then deny expiry/cancel/reclaim on both physical nodes.
3. **Independent revocation state:** A signed 1.5s status is still only bounded freshness. Do not promote for privileged execution without direct graph fencing or monotonic signed generation/revocation plus deny-on-unreachable status.
4. **Key and security:** Dedicated controller signing-key issuance, secure escrow/rotation, registered node token distribution and TLS/Tailscale API reachability must be audited first.
5. **Trace durability and operational readiness:** Retain existing fsynced node journals, encrypted and signed per-node NAS custody, restore checks and failure-injection gates; no generic shell.
6. **GitHub:** Local source branches may diverge from GitHub; reconcile ancestry and avoid mutating unrelated work until confirmed.

**Hard stop:** `ASSISTX_TRACE_LEASE_ISSUER_ENABLED=false`; production workers unchanged; no real execution grant was consumed.

## October 8 additional live-graph schema and rollback-only validation

A read-only inspection of the running AssistX Neo4j database (31,074 Task nodes) showed that the production task creator persists **`ticket_type` and `kind`**, not `task_type`. **No existing trace-probe tasks** were present. This was a real incompatibility in the initial review branch and has been corrected: lease admission requires both canonical task markers; any partial `trace_probe` marker is treated as a protected case rather than accidentally falling through to the LLM worker. A claimed live trace-probe is denied before journal preparation because the node-side two-proof online authority handshake remains unwired; no worker advertises `trace-probe`, including manual-capability overrides. The fixed shadow synthetic canaries remain separate.

**Real Neo4j / uncommitted transaction experiment:** On the running `assistx` database, a test harness created one short-lived synthetic Task per target **inside an explicit transaction**. The same transaction performed the claimed-state transition, signed a temporary in-process 10-second lease and fresh challenge/status based on the actual Neo4j row, and verified cancellation and changed-claim denial after graph mutations in that same transaction. **Every transaction explicitly rolled back**. An independent subsequent read confirmed zero fixture Tasks remaining for both `xwing` and `scotts-macbook-air`. No AssistX API, production worker, deployed issuer key or real command was involved. This validates graph transaction semantics, **not** a committed AssistX-issued real claim or live distributed revocation.

Reproduce only with explicitly scoped Neo4j credentials and review of target database: `scripts/trace_claim_lease_neotx_canary.py` runs `begin_transaction`, never commits, calls `rollback` in `finally` and verifies zero persisted IDs. The acceptance criteria for future independently repeated tests are `graph_claim_observed_in_tx=true`, `cancelled_denied=true`, `superseded_denied=true`, `rollback_confirmed=true`, `persisted_test_tasks=0`, `executed_commands=0` for each registered node. The experiment and these expectations were documented **after** the first exploratory run; do not misrepresent them as preregistered.
