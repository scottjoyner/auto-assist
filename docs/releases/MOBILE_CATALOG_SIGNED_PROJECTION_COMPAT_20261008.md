# CI release blocker: mobile Agent Auto signed runtime catalog import drift
Date: 2026-10-08 EDT. Base: Offline Safety RC2 draft #155.
Scope: fail-closed compatibility repair only; no mobile routing, provider selection, model traffic, signing-key provisioning or deployment.

## Prior prediction
- The runtime catalog's error boundary imported a `RuntimeProjectionSigningError` symbol that `runtime_projection_v2.py` does not export. Both the expired-projection and unexpected-backend-error paths therefore fail with `ImportError`, preventing safe degradation/clean 503 classification.
- `_current_runtime_projection` also requested `build_runtime_projection_v2`, while the actual Ed25519-backed module exports `build_runtime_projection`. This was a latent integration failure outside the two CI tests.
- Switching the call to the existing signed builder and catching its canonical `RuntimeProjectionBlocked` base class would preserve the requirement that the server has a valid signing key and no runtime is admitted from expired/unverifiable projections.

## Implementation
- `mobile_agent_routes._current_runtime_projection` delegates to `runtime_projection_v2.build_runtime_projection` (the existing builder). It continues to use the same TTL clamp and `_neo` dependency.
- The catalog handler recognizes only `RuntimeProjectionBlocked` as a safely degraded projection state and returns the existing sanitized zero-runtime response. Other exceptions still return HTTP 503 with a generic error and no backend detail.
- Two new regressions verify the canonical builder is invoked without opening a graph session, and missing Ed25519 signing key remains `RuntimeProjectionBlocked`, never a fabricated unsigned catalog. No source credentials or remote routes are involved.

## Observations
- Isolated x1-370 checkout of base `0e7e09f80c9686bf968d6c1c60f24b1b39843c3e`: two original failing `tests/test_mobile_runtime_catalog.py` tests reproduced before patch.
- After patch: **15/15 mobile catalog tests passed**, Python compilation and diff check passed. One existing Starlette TestClient deprecation warning; no provider or Neo4j calls.
- Production-like app import and physical phone acceptance are not established by this test. Source must remain a draft and pass GitHub's full test dependency CI.

## Safety and next gates
- Preserve client-visible `agent_auto_available=false` on missing/invalid/expired signed state; never mark a model ready from an unsigned response.
- Record a production-dependency ASGI fixture for valid signed/expired source and redaction before deployment. Staging auth provenance and Tailnet ingress identity remain separately gated.
- The separate tracked-env-file security issue #156 requires custody review. Do not include the env files or raw values in this PR.

**Decision:** draft independent compatibility fix; no production mobile deployment, no credential changes, no background worker execution.
