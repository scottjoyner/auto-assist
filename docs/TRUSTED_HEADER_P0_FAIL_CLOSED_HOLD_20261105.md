# P0 #149 — trusted-header auth fail-closed migration

**DRAFT ONLY — PRODUCTION HOLD / NO-GO.** Stacked on PR #551 exact parent df1bd6f5d38163ef93f93b41c0512b12b84ab0e0. This change is not safe to promote until legitimate proxy clients and rollback are reviewed.

## Existing source boundary

Before this patch, _auth_user_from_credentials accepted any nonempty configured TRUSTED_AUTH_HEADER from request headers BEFORE Basic. Issue #149 reproduced header-only /api/traces 200 in synthetic tests and the live localhost-bound API previously reported the variable present. This does not prove an external exploit or ingress injection; all alternate paths, header stripping and operator-trust provenance remain unverified.

## Source-only deny-by-default patch

- TRUSTED_AUTH_HEADER remains as a configuration migration indicator but never substitutes for Basic or independent machine identity proof.
- Operator Basic still authenticates with constant-time credentials, even with a forged header. Missing/invalid Basic denies before Neo4j.
- Fleet machine tokens remain identity-only on the separately scoped route; five legacy Task endpoints remain 403 for machine callers.
- A separately signed /api/voice/events callback remains signed; a spoofed proxy identity does not substitute.
- No ingress, credentials, containers, services or live graph change in this draft.

## Required independent cutover gates

1. Privately inventory every ingress to localhost :8000 (Docker peers, reverse proxies, tunnels and Tailnet). Identify which authorized clients rely on header-only auth, without publishing actual configured header names, secrets or process environment.
2. Authenticate every upstream independently and prove all ingresses strip caller-supplied identity headers. If header delegation remains necessary, use independently verified mTLS/signed proxy assertion; otherwise migrate legitimate clients to scoped credentials.
3. Exercise isolated staging negatives for spoofed headers and missing/invalid Basic at /api/traces, /traces, legacy Task GET/claim, and signed voice event. Prove unauthorized classes make no Neo4j calls.
4. Verify valid operator and proxy-client continuity and an immutable *security-safe* rollback image; do not treat the old insecure image as an acceptable rollback target.
5. Owner-authorized physical cutover and independent off-host denial are separate from hosted synthetic PASS.

All source tests leave PRODUCTION HOLD / NO-GO. No deploy or merge until independent ingress custody, legitimate client migrations and rollback are demonstrated.