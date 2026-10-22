# Hermes external high-water challenge — October 22, 2026

**Decision: HOLD / P0 #414 OPEN.** Research-only continuation of auto-assist draft PR #425. No production Hermes process, mounts, corpus, credentials or provider calls changed.

## Purpose and binding

The earlier two-signature checkpoint can still be replayed alongside an older signed corpus and image. This revision introduces an **opt-in, fail-closed live challenge** to a separately administered high-water authority. It implements the worker-side verifier and bounded UNIX-socket transport—not a deployed independent authority or a durable remote ledger.

Required opt-in:
- HERMES_REQUIRE_MOUNT_ISOLATION=true
- HERMES_REQUIRE_SIGNED_CORPUS=true
- HERMES_REQUIRE_CORPUS_CHECKPOINT=true
- HERMES_REQUIRE_EXTERNAL_HIGH_WATER=true
- HERMES_HIGH_WATER_SOCKET=/run/hermes-witness/authority.sock
- HERMES_HIGH_WATER_TRUST_KEY_PATH=/app/attestation/authority.pub
- HERMES_HIGH_WATER_OWNER=<approved owner>

At every checkpoint verification, including model-context revalidation, the worker generates a fresh 32-byte nonce and sends a bounded newline-delimited JSON request with schema, nonce, owner, generation, signed checkpoint SHA-256 and signed manifest SHA-256. A signed reply must echo all bindings and include a monotonic high_water_generation and boolean revoked. The worker verifies a third, pinned Ed25519 public key **different from both corpus and image-pinned checkpoint keys**. It accepts only revoked=false and exact generation equality. A higher high-water means the worker is stale and must be updated; any lower, missing, divergent, replayed or revoked result denies. Transport has a 1.5-second timeout and no cached-success/offline fallback.

A local AF_UNIX socket is a transport interface only. It does **not** independently attest state or custody. The actual witness and private signer must be controlled outside all worker/host credentials and rollback domains, with separate, auditable monotonic persistence. A socket on x1-370 or a machine controlled by the same SSH/account remains a research witness, not release authority.

## Validation

Tests in tests/test_hermes_high_water.py use ephemeral local synthetic signers and test-owned UNIX sockets. They prove nonce/owner/manifest/checkpoint binding, signature and key separation, no cached success, stale/higher generations, revocation and outage denial, and an old corpus+checkpoint rollback denied **while the synthetic responder retains newer high-water generation 2**. This demonstrates the proposed client protocol, **not** independently retained state. No real source data or credentials used.

The existing restricted rootless Compose candidate intentionally leaves external mode **off** (there is no approved independent witness connected). Its offline image overlay stages the updated modules with exact blob pinning and continues to pass 12/12 physical checks. The local combined focused suite passed **294/294** tests; hosted CI must be checked on the new commit separately.

## Remaining production NO-GO

An independently administered append-only/rollback-resistant high-water ledger, owner-bound authority signer and key lifecycle, revocation/fencing, hardened UNIX relay, multi-host full-worker co-rollback proof, a signed exact production image, approved narrow RO corpus, controlled isolated RW scratch, removal of broad production mounts, authorized real task receipts, and independently approved rollback/recovery remain outstanding.

**Do not merge or deploy PR #425 or close P0 #414 based on the local synthetic protocol. Production HOLD remains.**
