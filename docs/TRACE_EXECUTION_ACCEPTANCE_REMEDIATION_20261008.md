# Trace execution acceptance remediation contract — 2026-10-08

**Scope:** PR #119, `review/assistx-trace-gate-20261008`. Read-only evidence collection only. This document does not authorize execution, deployment, key access, claim issuance, or NAS writes.

## Evidence provenance

Each check must emit a record with `gate_id`, `observed_at`, `repository_revision`, `node` (if applicable), `provenance` (`unit_fixture`, `rollback_transaction`, `physical_shadow`, `deployed_observation`), `test_id`, `result`, `evidence_digest`, and `artifact_uri`. Missing or unverifiable evidence is **blocked**, never passed. Do not promote fixture evidence into deployed evidence.

## Gate matrix

| Gate | Evidence already reported | Minimum next read-only acceptance | Blocked until |
| --- | --- | --- | --- |
| Lease freshness | Signed short-lived lease and nonce-bound signed current-status fixture tests | Replay expired, future, skewed, nonce-mismatched, status-too-old and wrong-digest fixtures; report exact TTL and clock bounds | Authenticated deployed issuer and proven fence semantics |
| Revocation / supersession | Rollback-only Neo4j cancellation and supersession negatives on two node identities | Compare signed current status against latest persisted generation and cancellation; capture rollback evidence and zero persisted tasks | Committed claim and revocation-under-execution failure injection |
| Wrong node / replay | Negative fixture tests for identity, replay and marker consistency | Matrix of cross-node identity, reused nonce, swapped signature, conflicting marker and restart replay | Direct authenticated negative admission on each physical node |
| Production API | Isolated FastAPI/worker canaries | Inspect deployed route table, flags, process startup and health without changing services | Production startup proof with authenticated issuer and fail-closed outages |
| Signing-key custody | Protected file and signature fixtures | Inspect ownership, permission, path, verifier pin and rotation/escrow procedure metadata; never read private key material | Receiver-owned key provisioning, rotation and escrow evidence |
| Audit / NAS custody | Local hash chain and encrypted synthetic segment restore; physical journal preflight | Reconcile append-only receipt chain, head digests, mount identity and restore manifests; report real CIFS 0755 policy denial | Restricted CIFS ACL, real fsync/restore, two-phase recovery, independent WORM witness |
| Physical negative admission | Physical shadow preflight on xwing and Mac; no OS commands | Read-only attest both enable flags false, zero accepted production trace claims, intact journals and no task execution | Authenticated direct physical denial and crash/restart evidence |
| CI baseline | Focused security tests and dedicated workflow reported passing | Bind every workflow result to exact head SHA; capture broad CI failures and compare to main baseline | Broad CI green or independently justified, reviewed exception |

## Machine-readable artifact

Produce `trace-acceptance.json` with `schema=assistx.trace-acceptance.v1`, PR/head/base SHA, collection timestamp, per-gate records and `promotion_eligible=false` unless *all* mandatory deployed gates pass. A passing fixture may be represented as `fixture_pass`, never `deployed_pass`. Fail closed on missing fields, unknown provenance, stale evidence, unverifiable digests, or absent CI baseline.

## Non-negotiable holds

- `FLEET_TRACE_PROBE_ENABLED=false` and `FLEET_TRACE_REAL_EXECUTION_ENABLED=false` in production.
- Do not enable `FLEET_UNSAFE_SHELL_TASKS_ENABLED`, generic command execution, OpenTunnel, or unrestricted dispatch.
- Keep PR draft and unmerged pending acceptance.
- Preserve local journals, encrypted archives and NAS5 recovery contents; never truncate, delete or silently repair.
- Do not use the current `file_mode=0755,dir_mode=0755,nounix,noperm` CIFS mount for private trace offload.
- Signed current-status freshness (<=1500 ms) is not instantaneous revocation; any post-check race requires an explicit fencing design.

## Reconciliation note

Issue #120's older text describes the executor as an untracked prototype. The executor and worker wiring are now present in PR #119 at `a0ff576c875ecc65a7b2f768431bb832cc005083`; this **does not** establish production deployment or authorization. The latest PR body reports 194 local focused tests; verify exact test identity and workflow revision before promoting any count.

## Completion criteria for this slice

1. CI publishes a schema-validated, digest-addressed read-only acceptance artifact.
2. The report displays explicit blocked reasons and evidence provenance per gate.
3. Issue #120 and PR #119 reference the same authoritative acceptance artifact and revision.
4. No changes to production flags, live tasks, keys, NAS content or deployed services.
