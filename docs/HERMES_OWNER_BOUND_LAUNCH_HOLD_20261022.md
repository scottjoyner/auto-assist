# Hermes owner-bound pre-launch safety gate — October 22, 2026

**Decision: HOLD / research candidate**, draft auto-assist PR #425, P0 issue #414 OPEN. This change does not modify or restart production Hermes, protected data, credentials, or mounts.

## What changed

The existing strict-executor adapter already exchanged a bootstrap claim for a scoped task token, and the API middleware revalidated task lease/projection/owner at heartbeat and completion. However, the *local model subprocess launch path* previously checked the token's presence and a cached lease-lost event only, not the signed identity or owner again before spawning a subprocess.

New opt-in **HERMES_REQUIRE_OWNER_BOUND_LAUNCH=true** verifies the issued Ed25519 JWT against a separately supplied, immutable public key path **ASSISTX_EXECUTOR_VERIFY_KEY_FILE** (no private-key fallback), requires **ASSISTX_REQUIRE_OWNER_BINDING=true**, and checks canonical signed owner issuer/subject, task ID, claim ID, agent ID, projection generation, inference scope, allowed model alias, unexpired signed token, and currently active claim identity. It then performs a **fresh server-validated heartbeat before launching any model process** and repeats local signed-claim verification after the heartbeat. A rejected JWT, changed owner, bad signature, wrong model, missing key, stale claim, heartbeat failure, or withdrawn lease produces a bounded denial and does not spawn a child. A heartbeat failure fences the lease and terminates any existing child. The default remains opt-in disabled for untouched legacy production.

Only an actual HTTPS-authenticated issuer and correctly configured API-side live claim/owner verification can make this trustworthy. The worker cannot derive owner identity from a task payload or client-supplied header. Do not use a trusted public key from mutable corpus/scratch as production authority. Pending stronger fs provenance, image/boot measurement, key custody, and release acceptance, deployment HOLD applies.

## Physical and unit checks

The offline rootless Compose synthetic candidate sets:
- HERMES_REQUIRE_OWNER_BOUND_LAUNCH=true
- ASSISTX_REQUIRE_OWNER_BINDING=true
- ASSISTX_EXECUTOR_VERIFY_KEY_FILE=/app/attestation/executor.pub

The **public half only** of a fourth ephemeral research signing key is included in the disposable image; its private half is neither written to source nor mounted. The candidate image overlays exact pinned patched code from `strict_executor_adapter.py` and `hermes_launch_gate.py`, in addition to the previously signed corpus modules. It continues to enforce precisely two disposable mounts, read-only corpus and isolated writable scratch, no network, non-root execution, and no real task/provider/model calls. The normal-entrypoint two-stage restart + negative signed corpus replay checks still pass.

New local tests use synthetic signed JWTs to verify owner/claim/model tampering, expiry, wrong key/symlink, missing server owner-binding, lease revocation, no subprocess on heartbeat failure, ordering of fresh heartbeat *before* child creation, and preservation of default behavior when opt-in is absent. The real environment's optional async API pytest fixtures may require a separate plugin; do not report skipped plugin-dependent tests as passing.

## Remaining NO-GO requirements

No physical real signed task has been dispatched through production router/API. Token ownership, server-side task lease revocation and fencing across partitions, owner-bound execution receipts, separate signer/key custody and a rollback-resistant independent witness must be demonstrated against live authorized task infrastructure. Original Hermes still runs with **16 legacy mounts, nine writable**, including protected SSD ancestors, broad Git binds, NAS-labelled fallback and SSH mounts. An approved signed RO corpus source, separate authorized RW scratch, removal of every legacy bind, exact-image build/reproducibility, and independent rollback acceptance remain required.

**No production deployment or restart was authorized.**

## Real-image physical owner-gate rehearsal

The separate restricted candidate now exercises the actual signed JWT launch verification, successful mocked fresh heartbeat, changed-owner denial, and mocked heartbeat-outage denial on **both** normal container starts (13/13 physical checks). A missing `owner_identity.py` module was found and supplied as an exact pinned test-image overlay; the production image itself remains unchanged. See [HERMES_PHYSICAL_OWNER_LAUNCH_REHEARSAL_HOLD_20261022.md](HERMES_PHYSICAL_OWNER_LAUNCH_REHEARSAL_HOLD_20261022.md). This does not authorize production rollout.
