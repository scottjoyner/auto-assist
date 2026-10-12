# Signed mobile observation ASGI adapter — research only

**No production gateway changes. No endpoint registration. No signing key
provisioned. No live Redis replay authority. No iOS rollout.**

This is the smallest synthetic, receiver-side request-integration demonstration
above the independently owned [signed assertion verifier (PR #210)](https://github.com/scottjoyner/auto-assist/pull/210)
and [Redis replay-fence research (PR #212)](https://github.com/scottjoyner/auto-assist/pull/212).
A pure function, `verify_observation_asgi_identity`, can authenticate an ASGI
`GET /api/v1/runtime/observations` request **only** when supplied with:

- Exactly one canonical base64url encoded Ed25519-signed claim and one signature.
- An owner-pinned receiver public key and exact receiver key ID.
- A fixed `model:observe` operation scope, exact method and ASGI **raw path**,
  with no query or normalized-path trust.
- A separate, independently provisioned **atomic and durable** one-time nonce
  callback returning literal `True`, not a local in-process replay set.

Malformed/missing/duplicate headers, legacy `Tailscale-User-Login` alone,
wrong signer, wrong raw target, request method/scope mismatch, replay, nonce
store outage, and wrong canonical encoding are all denied. No Basic-auth
fallback is implemented; do not assume legacy header provenance from Tailscale
Serve or the container host's loopback binding.

A **synthetic FastAPI application** proves the intended behavior (401 without a
signed claim; 200 once with a synthetic signed claim; 401 on replay), returning
only a dummy observation response with `authority=not_admitted`, no models.
This proof is not a production route, no model can become selectable, and it
does not test actual user sign-in, Tailscale TLS, a gateway signer, or a real
Redis deployment. Real Redis 10-worker and restart-denial evidence is in PR #212.

## Migration and release NO-GO

The running x1-370 AssistX API still has an enabled legacy trusted-header
path with strict Basic disabled; its API shares two Docker bridge networks and
multiple peer containers. [Issue #149](https://github.com/scottjoyner/auto-assist/issues/149)
requires ingress isolation, a genuine Tailnet identity source, header stripping,
physical same-bridge negative acceptance, scope/role policy, and iOS Agent Auto
compatibility. [Issue #156](https://github.com/scottjoyner/auto-assist/issues/156)
owns historical credential custody and rotation decisions.

For a production implementation, first provision an isolated operator-owned
gateway that authenticates the real Tailscale user **before** signing and
strips incoming gateway-claim headers. Pin a fresh independent receiver key
outside source control, connect an approved durable nonce store whose
generation cannot silently advance on restart, implement scoped ASGI/HTTP
integration with explicit authorization and mobile client compatibility, then
witness forbidden-path, forged-header, stale/replayed and same-Docker-bridge
requests against an **isolated staging** topology. No test here certifies
those gates; leave all production admission/routing/release flags on HOLD.

The dedicated offline `rc2-bridge-auth-negative` CI lane tests this module
without Docker, production API imports, HTTP network calls or provider tokens.
Its green status is a *source contract*, not deployment authorization.

## Optional disposable end-to-end acceptance

The existing PR #212 guarded real-Redis runner is extended *in this stacked
research branch only* to instantiate a synthetic FastAPI observation route
using this signed-ASGI adapter, connected to the same real Redis nonce fence.
With its explicit `--approve-disposable-replay`, exact clean source head and
immutable preinstalled image SHA pins, it runs one read-only/network-none,
host-port-free Redis container with synthetic keys and a temporary TestClient.

Acceptance requires **ten simultaneous signed HTTP observation requests**:
exactly one HTTP 200 and nine HTTP 401 denials. Missing/legacy forged
Tailnet headers must also get 401. The disposable Redis is then restarted
without persistent data; an old signed HTTP assertion must still get 401
because the receiver's pinned Redis generation changed. This does not prove
durable anti-rollback on re-provisioning a new generation, nor does it
authenticate a real gateway/Tailscale client. The runner only cleans up its
own exact container identity and refuses pre-existing names; there are no
API containers, host ports, NAS paths, or production credentials involved.
