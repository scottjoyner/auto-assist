# Signed mobile runtime catalog compatibility — preregistered 2026-10-08

Pinned source base: auto-assist main a300072d11787a58a4587fa325f1b8fd66803730.

**Baseline:** 2 mobile-catalog test failures, 14 focused tests passing.
In the expired-projection and generic-backend failure paths the mobile route
imports RuntimeProjectionSigningError from runtime_projection_v2, but that
module does not export the symbol. A second independent mismatch exists:
mobile _current_runtime_projection imports build_runtime_projection_v2,
but the module exports only build_runtime_projection. Those are bugs at a
read-only catalog safety boundary.

**Prediction and design:** Restore a typed signing exception that inherits
the existing RuntimeProjectionBlocked, and restore the legacy v2-named builder
as a direct pass-through to the existing Ed25519-signed builder with identical
parameters. The wrapper must not call the old unsigned projection directly.
Missing, invalid, inaccessible or over-permissive signing key must fail
closed; it may return a sanitized empty read-only mobile catalog, not any
unapproved models, and must never include backend errors or secrets.
Unexpected backend failures return generic HTTP 503, not HTTP 200.
No runtime admission, provider quota, signed claims or dispatch authority.

**Acceptance:** Existing runtime_projection_v2 + mobile_runtime_catalog
suites, plus positive Ed25519 signature verification through the restored
v2 function, and missing-key denial. Use only injected synthetic private
keys and FastAPI TestClient, not live model or network endpoints.

## Observed results after preregistration

The existing mobile/runtime tests were initially 14 passing, 2 failing
due to ImportError of RuntimeProjectionSigningError. After restoring
the typed blocked-state symbol, the mobile-named Ed25519 builder, and
sanitized failure for a missing signing-key file, the existing test
suites plus three new negative/positive cryptographic fixture tests
passed **19/19**. The positive check uses a generated Ed25519 test key
and verifies the signature over the v2 signing message. Missing key
material and inaccessible files reject rather than permitting v1
or unsigned metadata. No real runtime, provider, dispatch or key was used.
GitHub full-CI and combined release candidate acceptance remain separate.
