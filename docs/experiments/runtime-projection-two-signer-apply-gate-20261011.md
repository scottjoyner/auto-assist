# Signed canonical runtime-projection apply gate — research integration (2026-10-11)

**DRAFT-ONLY. Do not deploy, run `--apply`, mint keypairs, insert runtime records, renew leases, or promote routing on the basis of this PR.**

Context: [AssistX P0 issue #304](https://github.com/scottjoyner/auto-assist/issues/304) (verified stale source/reference replay), [Auto-Router issue #52](https://github.com/scottjoyner/auto-router/issues/52) (expired canonical), [AssistX draft #303](https://github.com/scottjoyner/auto-assist/pull/303) (read-only review preflight), [auto-router draft #53](https://github.com/scottjoyner/auto-router/pull/53) (physical Beelink preflight).

## Evidence motivating the change

Existing `scripts/approve-runtime-projection.py` enforces a short maximum canonical TTL, generation compare-and-swap, explicit operator `--apply`, and structural schema checks. An independent validation-only Fleet Commander experiment demonstrated that a copy of the now-expired generation 643 manifest changed in memory to proposed 644, using placeholder reviewer/approval text and the original evidence references, nevertheless passed `validate_manifest()`. That does not bypass generation CAS or establish an unauthorized production write. It proves that checking nonempty evidence references alone cannot establish their freshness, provenance or independent operator review.

The new `scripts/runtime_projection_signed_apply_gate.py` is a deliberately strict proposal for the **actual apply boundary**. Its only inputs are the original manifest, a file containing two independent Ed25519 signed messages, and two *separately configured and trusted public keys*. **There is no built-in signer or key generator.** No source or operator signing keys are configured or obtained by this study.

## Trust model

The source attestation public key must be provisioned and pinned by the trusted platform/host-evidence owner; the operator review public key must be separately provisioned by the authorized approver authority. They **must not be the same key** and must not be selected from input manifest, unsigned JSON, URL, operator-supplied arbitrary file path, or user string. Simply holding two locally generated keys is **not independent authority**. Production deployment needs proof of separate signers, secure private-key custody, authorized operator identity and key rotation/revocation policy.

Proposed operator environment paths (public keys only; private keys never in this process):

- `ASSISTX_RUNTIME_SOURCE_PUBKEY_FILE`
- `ASSISTX_RUNTIME_OPERATOR_PUBKEY_FILE`

Both resolve to absolute, nonsymlinked, non-group/world-writable files containing a newline-terminated hex encoding of an Ed25519 32-byte raw public key. The verifier refuses missing, same, malformed or untrusted identity. Independently review the mapping of each public key to a real trusted producer/reviewer before rollout. **No real public/private signing keys were created during these changes.**

### Signed source envelope

Ed25519 signs exact canonical sorted compact UTF-8 JSON of a strict `source.payload`, including a fixed `audience`, `role=source-observer`, SHA256 of the **complete exact reviewed manifest JSON**, proposed generation, approval ID, observed/issued and expiry times, and one observation per runtime:
- canonical runtime, node and running process ID;
- physical/model/capacity original observed timestamps, each ≤5 minutes old;
- exact independently loaded model artifact fingerprint list and maximum observed safe slots;
- an observation per declared private access transport with a SHA256 of the actual URL, direct completion success, HTTP 200, and original canary timestamp ≤3 minutes old.
- Source issue and expiry bound to ≤5 minutes, with each observation made **before** the source attestation was issued (allowing at most 5 seconds clock skew).

This does not make a socket test a cryptographically signed host execution receipt unless the **designated source verifier** genuinely performs and independently authenticates that observation. An in-band source field is not authority.

### Signed independent operator envelope

Ed25519 signs `operator.payload` containing the same audience, `role=operator-reviewer`, **same complete manifest SHA256**, same generation and approval ID, reviewed `approved_by`, and the SHA256 of the *entire signed source envelope*. The independent review must occur after source issuance, and the operator's signature is valid for **no more than 120 seconds** for the apply attempt. It can never be reused for a different manifest, model, process, path, generation or approval ID.

Both signatures are revalidated **at the start of every Neo4j transaction invocation/retry**, before the first Cypher write. A draft internal `RuntimeProjectionApprovalReceipt` node records the approval ID, generation, source signature digest and source/reviewer original timestamps atomically with the canonical generation and four observed evidence classes. Reuse of a recorded approval ID fails. **The code now refuses any `--apply` before writes unless live Neo4j reports BOTH uniqueness constraints:** `RuntimeProjectionApprovalReceipt.approval_id` and `FleetProjectionState.name`. The graph's other constraints do not satisfy this requirement. The schema check runs from a trusted database query, not from unsigned input. Transaction checks remain separately required, and a concurrency/rollback pilot is STILL a release gate.

A read-only x1-370 query confirmed **53 existing UNIQUENESS constraints, but NEITHER required constraint exists** on the current AssistX database. **No schema mutation was attempted.** The designated operator/database maintainer must review and plan migrations; representative Neo4j 5 Cypher (DOCUMENTATION ONLY, never executed):

```cypher
CREATE CONSTRAINT canonical_fleet_projection_state_name_unique IF NOT EXISTS
FOR (s:FleetProjectionState) REQUIRE s.name IS UNIQUE;

CREATE CONSTRAINT runtime_projection_approval_receipt_id_unique IF NOT EXISTS
FOR (r:RuntimeProjectionApprovalReceipt) REQUIRE r.approval_id IS UNIQUE;
```

Before applying migrations, check for existing duplicates and concurrent writers, snapshot/backup, obtain DBA/operator authorization, verify staging negative tests, and schedule explicit rollback. Do not allow a research PR to silently create constraints in production.

### External CLI behavior

Dry-run of a normal manifest remains a *structural* dry run; the tool does not imply signed approval. For `--apply`, the command now **requires** `--signed-attestations /private/approved-source-and-operator-receipts.json` and the two provisioned public-key files. Missing/malformed/expired/unsigned/untrusted receipts **refuse before connecting to Neo4j**, and rechecking inside the transaction prevents retry-time expiration bypass.

**DO NOT run this sample as an actual apply** until independent signers, trust anchors, migration/revocation policy, stage negative tests and operator approval are completed:

```text
approve-runtime-projection.py <newly-reviewed-manifest.yaml> \
  --apply --signed-attestations <two-signed-receipts.json>
```

The original `--apply` without an attestation bundle must now fail. Existing raw historical approval bundles are intentionally incompatible with the new authorization requirement; do not work around it by passing `--apply` through another script.

## Test scope

`tests/test_runtime_projection_signed_apply_gate.py` creates **ephemeral test-only Ed25519 keypairs** in memory and tests:
- valid separate source/operator cryptographic signatures;
- altered/missing signatures, same signing key, reviewer substitution, wrong generation, wrong process, model swap and private path mismatch;
- replay of signed source under a different operator signature, stale/future-dated observations and canaries, stale operator review, capacity above signed bounds;
- signed wrong-schema/input field refusal;
- **no Neo4j connection** on `--apply` without signed receipts;
- **zero transaction calls** when signatures fail;
- CAS mismatch and prior approval-ID replay refusal **before retiring any admitted rows**;
- mock-only application writes source/reviewer original timestamp metadata and a replay receipt in one transaction. This is an in-memory fake transaction, **not a real Neo4j integration or physical device proof**.

Local source-only test count: **30 new security tests plus 22 inherited review-only tests passed; strict Ruff passed**; independent hosted workflow tracks exact branch head. Existing broad AssistX CI has many unrelated inherited failures and must not be described as globally green.

## Remaining release NO-GO

1. **Independent signer integration**: establish actual trusted runtime telemetry and source receipt producers; isolate their private keys; bind operator authorized identity to a separately trusted key and audit approval issuance, key rotation/revocation.
2. **Physical provenance**: connect actual loaded model digest, process PID+boot identity, direct private-path completion, observed capacity and current owner/lease/permission to the source signer. A plain PID and model hash on one machine are not enough; stale process restart must invalidate observations.
3. **Schema/replay migration**: transaction uniqueness constraint, concurrent approvals, crash rollback, release compatibility, legacy caller inventory, audit retention. Do not silently enable a new graph label on a live database without approval.
4. **Short-TTL re-review**: source attestations are valid no more than 5 minutes; operator review no more than 2 minutes. No automated lease renewal without separately authorized delegated reviewer rights and bounded revocation.
5. **Separate routing gates**: physical per-user receipts (Auto-Router #37), current AssistX and router projected runtime freshness, staged authenticated provider catalog (AssistX #288/Auto-Router #41), approved admission and iPhone/Destroyer evidence.
6. **Operator authorization** before any new canonical generation. Historical Beelink 643 was one-time approved and expired; it is **not** authority to issue 644 or beyond.

This PR is a protocol and fail-closed enforcement candidate, **not a deployment-ready security release**.
