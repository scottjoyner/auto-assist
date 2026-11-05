# P0: standalone swarm endpoint authentication must fail closed

**DRAFT, production HOLD.** Stacked on auto-assist PR #552 exact SHA 705dc87f024c017faa0c35ae9bf3863b2921a2d6.

## Source finding (not a demonstrated deployed exploit)

The standalone swarm_routes.py defined `_default_auth` which accepted any Basic username without checking its password if the main API auth dependency had not been injected, and returned `system` for entirely anonymous callers. Its shared router contains sensitive trace GETs and graph/Task/model-endpoint writer routes. `_optional_operator_auth` also trusted any supplied Basic username without verifying the password when injection was absent. The canonical voice path performs a separate raw-body HMAC check only when the optional operator dependency returns no valid operator.

In the normal assembled API, `api.py` calls `set_auth_dependency(auth)`. This finding concerns unassembled/reused imports, source drift and code paths with missing injection; a live unauthenticated production exploit was NOT independently established.

## Patch

- Without an injected and separately verified auth dependency, ordinary swarm routes return HTTP 401 before graph, Task or model operations, even if a client supplies arbitrary Basic username/password.
- Even if an injected dependency returns blank or None rather than a verified operator identity, ordinary swarm routes deny with HTTP 401. A 403 from a valid injected policy propagates.
- Optional voice operator auth returns None instead of trusting any username when injection is missing, invalid or blank; the canonical voice handler then invokes its independent raw-body signature verifier, so unsigned callbacks are not granted operator identity.
- Reuses the preexisting verified assembled API authorization on legitimate routes without copying Basic credentials or enabling unverified proxy headers.
- Dependency-free behavioral source tests execute the exact AST of the source functions with synthetic stand-in types. They check anonymous, fabricated Basic, blank injected principal, legitimate verified delegate, 401/403 behavior, voice signature path and core trace/graph Task handler dependencies.

## Independent acceptance still required

Exact-head CI and synthetic assembled FastAPI 401/403/valid-operator/voice-HMAC tests, plus owner-reviewed ingress and gateway provenance, without accessing live graph data. This code must not bypass real Task claim fencing, external COMMIT authority, Context graph RBAC or production rollback. No host config, app runtime, graph, token, service or image was changed. No merge/deploy until separate production security gates are accepted.