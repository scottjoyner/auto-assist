# Hermes signed read-only corpus admission — October 22, 2026

**Decision: HOLD / research candidate only.** Related to draft auto-assist PR #425 and unresolved P0 issue #414. The production Hermes container, protected SSD evidence, NAS5 mounts, runtime credentials, and real providers remain unchanged.

## Newly tested admission contract

The earlier strict guard checked whether the kernel exposed an exact read-only knowledge bind and a different writable workspace bind. That alone could not tell whether the RO corpus contained approved, unchanged information: a privileged host operator can change backing bytes even when the container-side bind is read-only.

The newly added optional **HERMES_REQUIRE_SIGNED_CORPUS=true** gate requires strict mounted split-root isolation and a separate trust-root public key path provided by the reviewed image:

    HERMES_REQUIRE_MOUNT_ISOLATION=true
    HERMES_REQUIRE_SIGNED_CORPUS=true
    HERMES_CORPUS_TRUST_KEY_PATH=/app/attestation/trust.pub

The startup verifier reads a bounded manifest named **.hermes-corpus-manifest.json** inside the RO input, verifies a canonicalized JSON payload with Ed25519 and a 32-byte pinned public key outside the corpus/scratch mounts, and verifies every explicitly signed MD/TXT input against SHA-256 and size. It rejects missing manifests, unsigned MD/TXT files, extra nested directories, symlink aliases, duplicate entries, malformed signatures and unexpected paths. Limits are currently 1,024 files, 4 MiB per file, and 64 MiB total verified bytes, suitable for a scoped knowledge subset rather than a complete research archive.

The prior opt-in behavior is preserved when the signed mode is absent. Requiring signed mode without kernel mount isolation or split roots fails closed.

## Real physical research evidence

Fleet Commander built a disposable offline image overlay from the original Hermes image with the **actual updated production adapter code** and a fresh synthetic-only Ed25519 public verification key baked into the overlay. An ephemeral signing private key was generated in process memory solely to sign synthetic test inputs; **the private key was not put in a repository, manifest, image, container, or scratch mount**.

The isolated Compose candidate ran as UID 1000, network none, read-only rootfs, dropped capabilities, no-new-privileges and exactly two synthetic mounts: RO corpus and RW scratch. Two full normal rootless-entrypoint/strict-executor starts verified signed input and persisted fake self-task/evaluation output into RW scratch. Then the verifier's research runner modified *only* the temporary synthetic host-side corpus bytes and launched a third startup. That startup failed nonzero before any model/provider/task dispatch; previous output state remained at stage two.

**Result: 11/11 physical checks PASS.** All disposable Docker objects and synthetic scratch were removed. Focused negative tests cover forged signatures, changed data, unsigned files, wrong trust keys, path traversal, symlinks, too-large files and missing signed/strict mode.

## What this does NOT prove

- The synthetic ephemeral signer is controlled by the same research operator, not an independent signing authority. There is no independently signed production corpus or anchored, rollback-resistant checkpoint yet.
- **Startup verification is not continuous enforcement:** files could change after startup verification, and TOCTOU or remount attacks by privileged host actors need separate guards. The exact signed corpus should ultimately be verified again when consumed (or accessed from immutable verified snapshot storage).
- Manifest signatures have no trusted epoch, monotonic version/high-water mark, independent revocation, ownership receipts or protected key rotation. A correctly signed but stale or revoked manifest could be replayed.
- A pinned public key inside an image does not establish signed independent image/source provenance, nor owner-authorized live tasks, independent administrative custody, healthy NAS5 file contents or production rollback.
- The running production Hermes still has 16 mounts with nine writable, including two writable aliases enclosing the protected SSD knowledge corpus, a NAS5-labelled local-root path, mutable Git/source mounts and private SSH authorization surfaces.

Production **HOLD** remains. Keep draft PR #425 unmerged and P0 #414 open until independent signer/key custody, replay protection, verified source/image, narrow production mount migration, owner-bound grants and revocation/fencing, and tested rollback have all been approved.

## Point-of-use follow-up

Signed context reads now re-verify captured bytes, and the legacy semantic-memory index is blocked in signed mode pending a manifest-bound index. See [HERMES_SIGNED_CONTEXT_CONSUMPTION_HOLD_20261022.md](HERMES_SIGNED_CONTEXT_CONSUMPTION_HOLD_20261022.md). Independent monotonic replay prevention and host custody remain unqualified; deployment HOLD is unchanged.
