# Hermes signed checkpoint replay preflight — October 22, 2026

**Decision: HOLD / RESEARCH ONLY.** Applies to draft auto-assist PR #425 and P0 issue #414. The running production Hermes image, mounts, corpus, SSH material, NAS5 state and external provider connections were not modified.

## Scoped change

The real adapter's existing signed-corpus verifier now supports an additional **opt-in signed checkpoint**:

    HERMES_REQUIRE_MOUNT_ISOLATION=true
    HERMES_REQUIRE_SIGNED_CORPUS=true
    HERMES_REQUIRE_CORPUS_CHECKPOINT=true
    HERMES_CORPUS_CHECKPOINT_PATH=/app/attestation/checkpoint.json
    HERMES_CORPUS_WITNESS_KEY_PATH=/app/attestation/witness.pub

The checkpoint payload (schema 1) includes a positive 64-bit `generation`, the exact corpus `root`, SHA-256 of the **full signed manifest bytes**, and SHA-256 of the corpus signer's 32-byte public key. A second, distinct Ed25519 witness key signs this checkpoint's canonical JSON payload. The adapter fails closed if the checkpoint, witness key or manifest is missing, malformed, tampered with, has a mismatched hash/root/key, uses an untrusted key, or resides in the configured mutable corpus or scratch tree. Checkpoint mode requires both signed corpus mode and strict RO/RW mount isolation.

The normal signed-corpus mode remains opt-in and unchanged when the new checkpoint requirement is absent. This is **not** an automatic production mode switch.

## What was physically exercised

Fleet Commander on x1-370 used an offline Docker overlay built from the exact still-running Hermes image, adding only reviewed candidate Python files and synthetic-only test artifacts. The canary generated **two distinct ephemeral private signing keys in process memory**: one signer for the synthetic corpus and one signer for the synthetic checkpoint. Only their public keys, and the newer signed checkpoint, were baked into the disposable test image.

- Two independent normal rootless `/entrypoint.sh` to `assistx.strict_executor_main` starts accepted the current signed synthetic corpus and produced/persisted synthetic self-task and evaluation scratch output.
- A following startup presented an **older but genuinely signed corpus manifest with its exactly matching older files**. The pinned **newer** witness checkpoint's manifest hash did not match, so the adapter exited nonzero before agent task dispatch.
- After restoring the approved current signed manifest, the test modified its synthetic host-side source bytes. Another startup denied that ordinary tampering.
- Container had no network, no production binds, no real models/providers/tasks, read-only rootfs, non-root UID, dropped capabilities, no-new-privileges and exactly one synthetic RO input plus one synthetic RW output mount.
- All disposable Docker containers, images and synthetic directories were removed. Production Hermes remained running without a restart.

**Results:** 12/12 physical acceptance checks PASSED; combined focused Python regression suite **259/259 PASSED**. The test suite includes **31 new checkpoint-specific synthetic cases**: accepted exact witness, missing/different keys, bad signature, wrong root/digest/generation, malformed modes, stale manifest replay, and a deliberate co-rollback case.

## Essential negative finding: co-rollback still succeeds

The checkpoint is only as fresh as the **independently trusted anchor** that supplies it. A unit test deliberately rolls back the data, fully valid signed manifest **and matching signed witness checkpoint** to an old snapshot. With both private signing roles controlled by the same synthetic test operator, verification accepts this consistent old state.

This is **evidence of a remaining security gap**, not a test failure: the code has no independent append-only high-water database, no trusted external monotonic generation source, no protected checkpoint publication or user-authorized revocation receipts. A privileged operator who can replace the runtime image/checkpoint and signer trust roots can still defeat a merely image-pinned witness. The separate synthetic key is a separate cryptographic role, **not separate administrative custody**.

Do not label this implementation rollback-resistant or independently attested. A real release needs a **separately controlled, immutable/append-only and independently verified high-water authority** plus revocation, key rotation, owner binding, checkpoint freshness, publication/acknowledgment, and negative multi-host replay tests.

## Operational blockers remain

Production Hermes continues to use its original image `sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307`, with **16 mounts, nine writable**, including two aliases enclosing protected SSD evidence and an NAS5-labelled path that resolves to the local filesystem on x1-370. Real corpus custody, independent signing and image provenance, owner-granted ingest/dispatch, fencing/revocation, rollback, and deployment approval are not established.

**DO NOT merge, restart, remount, copy protected data, promote a signed checkpoint, or close P0 #414** solely from this synthetic research proof. Keep PR #425 draft and production deployment on HOLD.

## External nonce-challenged authority follow-up

An opt-in worker-side protocol now requires a fresh signed high-water response bound to nonce, owner, checkpoint and manifest hashes. Same-host research tests prove protocol behavior but not external custody; see [HERMES_EXTERNAL_HIGH_WATER_PROTOCOL_HOLD_20261022.md](HERMES_EXTERNAL_HIGH_WATER_PROTOCOL_HOLD_20261022.md). Production HOLD unchanged.
