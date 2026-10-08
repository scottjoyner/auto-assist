# Node-bound Ed25519 synthetic execution grants — xwing + MacBook Air

**Date:** 2026-10-08 America/New_York.
**decision_purpose:** Progress from untrusted synthetic task identifiers to cryptographically node-bound execution grants while preserving AssistX/Neo4j as the sole future live assignment authority.
**date_decided:** 2026-10-08.
**decided_by:** User requested continuing the two-node trace execution project; engineering decision strictly limits this slice to shadow-only synthetic no-op commands.
**decision:** Maintain a separate controller-held Ed25519 signer **per physical node** and a pinned verification public key deployed only to that node. Require signed, node-specific, 30-second synthetic grants before in-process no-op execution; add signed-grant SHA-256 to prepared/completed audit records; deny replay/expiration/wrong-node/forgery before any new trace receipt.
**review/reopen:** Only real node-bound, revocable AssistX/Neo4j claim issuance, a freshness/revocation gate, hard fail-closed persistent execution proof, and full physical negative validation may authorize a real typed command.

## Authority boundary and prediction

This is NOT a real AssistX claim. Every grant carries `issuer=offline-shadow-test-fixture`, `schema=assistx.synthetic-shadow-grant.v1`, `command_id=probe.noop.v1`, and `expires_at_ms <= issued_at_ms+30000`. The fixed remote verifier reports `assistx_claim_verified=false`, even on successful execution. Neither node runs a new daemon or consumes live tasks.

**Hypothesis:** Ed25519 authentication, per-node public key pinning, short grant TTL, exact task/claim derivation and the existing durable receipt chain can prove that each remote synthetic executor ran an approved no-op and rejected incompatible grants.

**Exploratory prediction:** A signed no-op should append exactly two receipts; an exact replay, expired grant, wrong node or altered signature should yield a denial without journal mutation.

This document records exploratory expectations and **observed results after the fact**, not a pre-registered prediction for the completed work. Use its expectations as the prospective gate for an independently repeated experiment.

## Implementation

- `src/assistx/trace_shadow_grant.py`: strict Ed25519 signed grant schema, canonical serialization, TTL and clock skew checking, fixture-only issuer, node/claim lineage and optional explicit local revoked-ID check.
- `scripts/trace_shadow_grant_bootstrap.py`: idempotent per-node Ed25519 generation and public key pinning. Controller-only private signers under `/home/scott/.config/fleet-trace-shadow/<node>/grant-signing-key.pem` (0600); private keys NEVER copied to remote nodes, NAS, or Git.
- `scripts/trace_shadow_grant_node.py`: fixed, input-only signed grant verifier; reads pinned public key from `<release_root>/config/grant-authority.pem`; supports only `probe.noop.v1`; always returns `assistx_claim_verified=false`.
- `scripts/trace_shadow_grant_control.py`: exact node choice, controller signing, and existing host-key-checked Tailscale SSH. No arbitrary command or destination argument.
- `scripts/trace_shadow_grant_negative.py`: physically validates acceptance, replay denial, expiration, wrong-node and signature tamper denial; checks journal increased exactly by 2 for its one permitted no-op.
- `src/assistx/trace_execution_adapter.py`: records validated grant digest with both durable receipts; no change to other fleet task classes or production routing.

Node-specific public key file:
- xwing: `/home/scott/.local/opt/assistx-trace-shadow/v1/config/grant-authority.pem`
- MacBook Air: `/Users/scottjoyner/.local/opt/assistx-trace-shadow/v1/config/grant-authority.pem`

## Recorded observations

**October 8, 2026, controller x1-370:**
- Both node-specific public keys were staged after verifying Mac Python 3.9 and Linux Python 3.12 Ed25519 support through installed cryptography libraries.
- The original accepted signed probes proved both physical nodes could verify a 30-second signed grant and durably record its SHA-256 in `prepared` and `completed` rows.
- **xwing:** final **10** records (5 preparations + 5 completions); **MacBook Air:** final **8** records (4 + 4). Extra xwing pair resulted from the first negative-runner attempt, which found and fixed a denial-response field mismatch; it was NOT an unauthorized command. It is preserved, not erased.
- Both negative-runner cycles returned `replay_rejected=true`, `expired_rejected=true`, `wrong_node_rejected=true`, `tampered_signature_rejected=true`, and `negative_attempts_added_no_receipts=true`.
- Distinct node journals have **three** encrypted NAS generations each, and independent `--verify-archives-only` checks passed all 3 per node. Older snapshots remain retained.
- Latest journal heads: xwing `e482e4f6e8505a41ed9f74985a1ac63640c019596eb5b5464b73813bb833d962`; MacBook Air `6fa83e10a4ec4f5c47f0db5af4b7695a7f41e9c3ba48cd47fdf12e1c15d727be`.
- Local test suite for signing, custody, node-path constraints, shell-disable and fleet-executor regression reported **65 passed** after bootstrap/idempotence and denial-response corrections. New code passed Ruff checks and static compilation. Rerun after all edits before changing scope.

## Reproduction

```bash
cd /home/scott/git/wt-assistx-trace-execution-20261007
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_shadow_grant.py tests/test_trace_execution_shadow_backup.py \
  tests/test_trace_execution_adapter.py tests/test_trace_execution_shadow_paths.py \
  tests/test_fleet_node_shell_gate.py tests/test_fleet_node_recovery.py \
  tests/test_safe_fleet_executor.py tests/test_fleet_executor_concurrency.py
# Verify existing archives without executing another synthetic no-op:
for node in xwing scotts-macbook-air; do
  PYTHONPATH=src:scripts python3 scripts/trace_execution_shadow_backup.py \
    --config config/trace_execution_shadow_nodes.json \
    --node "$node" --verify-archives-only
done
# Explicitly run another signed synthetic-only attempt (adds 2 journal rows):
PYTHONPATH=src:scripts python3 scripts/trace_shadow_grant_control.py \
  --config config/trace_execution_shadow_nodes.json \
  --node xwing --run-synthetic-noop
```

## Remaining gates before real commands

1. **Authentic AssistX claim:** These signatures are independent shadow test authority. A real claim must come from an authenticated current AssistX/Neo4j reservation, with a lease ID, expiration, claim generation and node identity verified by the node before execution. Do not allow this fixture issuer in production.
2. **Current distributed revocation:** The verifier exposes an optional revoked-ID argument for offline tests but the deployed node runner does not load a live signed revocation projection. Do not claim remote revocation enforcement. Define signed generation/freshness, monotonic rollback resistance, and failure behavior for control-plane outages first.
3. **Private signer custody:** Key recovery escrow and rotation are still untested; backup snapshots hold only journals.
4. **Fault injection:** Full disk, torn fsync, crash between receipts and network interruption still require node-specific drills.
5. **External immutable anchoring:** The local signed heads are not third-party WORM evidence. External anchoring remains blocked.
6. **GitHub reconciliation:** Local branch and commits are not necessarily published to remote history. Do not push/merge without verifying common ancestry and reviewing the existing mainline.

**Hard stop:** No services restarted, no arbitrary shell, no OpenTunnel, no agent auto-start or real command/claim dispatched in this slice.
