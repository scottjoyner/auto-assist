# Hermes owner-bound execution: real-image synthetic launch rehearsal

**October 22, 2026 — Research HOLD / P0 #414 OPEN.** This extends draft auto-assist PR #425. The real production Hermes container and all its data, mounts, credentials, services, models, and networks were left unchanged.

## Why this rehearsal matters

Prior unit tests validated the new opt-in signed owner-bound launch gate but the physical Compose test only confirmed that the guard was installed. A first new physical run exposed an actual missing module in the running production image: `assistx.owner_identity`. The canary failed before launching anything. We remedied this *in the isolated candidate overlay only* by SHA-pinning and overlaying the reviewed `owner_identity.py`, rather than altering production or skipping the test. The candidate also includes the already-reviewed `strict_executor_adapter.py` and `hermes_launch_gate.py`.

## What was physically checked

Fleet Commander on x1-370 built a disposable image from the exact running production Hermes image `sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307` with Docker `--network=none`, a restricted Compose profile, non-root UID, an immutable root filesystem, no additional capabilities, a **synthetic-only** RO corpus bind, and an isolated synthetic RW scratch bind.

A fourth **ephemeral synthetic private Ed25519 signer** was created only in the host canary's process memory. Only its **public verification key** was baked into the test image. A short-lived, wholly synthetic signed JWT was written to the disposable test workspace for the test to consume, not to the production filesystem or real API.

On each of two separate normal `/entrypoint.sh -> assistx.strict_executor_main` container starts, the **actual overlaid adapter and real token verifier** executed all three test cases:

1. **Authorized synthetic claim:** verified JWT, owner issuer+subject, claim/agent/projection identity, model alias and expiry; a mocked 200 heartbeat was observed **before** a mocked local model-process constructor (which never starts a real process).
2. **Mismatched owner:** changed unsigned cached owner claim while retaining the signed token; launch failed closed and the constructor was not called again.
3. **Heartbeat outage:** a separate valid synthetic claim received a mocked connection error; launch was denied and the executor lease was fenced without invoking even the synthetic constructor.

All network calls and process launches were explicitly patched inside this isolated test. These checks are **not** real owner enrollment, a live provider request, a production task receipt, or proof of trustworthy gateway custody. The original signed corpus tests, older signed-manifest replay denial, source RO protections, two-run scratch persistence, and disposable Docker cleanup also remain active.

**Results: 13/13 physical checks passed**, including an independent required check `signed_owner_positive_negative_and_heartbeat_denied_in_image` which must print the signed-claim proof marker once on each normal entrypoint start. No production restart or mount changes occurred.

## Remaining no-go gates

This research candidate deliberately has no network connectivity and no real model dispatch. Its heartbeat is simulated and not the production API's live owner binding. An independently administered durable, rollback-resistant high-water authority and revocation/key custody remain unqualified. Production still exposes 16 legacy binds (9 RW), including protected storage ancestors and mutable code paths. Real owner-bound execution receipts, live multi-user test, revoke-during-execution and network-partition fencing, approved narrow mount migration, exact signed release build and rollback must be validated separately before deployment.

**Keep PR #425 draft, issue #414 open, and production on HOLD.**
