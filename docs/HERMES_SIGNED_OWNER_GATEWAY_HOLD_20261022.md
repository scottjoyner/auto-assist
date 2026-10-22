# Signed gateway owner provenance — opt-in research handoff (2026-10-22)

**Decision: HOLD / NO-GO.** Draft auto-assist PR #425 and P0 issue #414. This source-only change was **not deployed** to running AssistX, did not restart Hermes, did not mutate production tasks, and did not create a production authentication key.

## Why this is needed

The production task-create endpoint supports opt-in strict owner scoping using `authenticated_owner(request.scope, auth_user)`. It refuses owner identity inferred from a task payload, an unsigned owner header, or worker agent identity. On the examined source, however, there was no upstream middleware that cryptographically validated a gateway principal and populated `scope.state.verified_owner`. Simply enabling `ASSISTX_REQUIRE_OWNER_BINDING=true` would therefore block newly owned tasks without a complete trusted authentication chain. The live API has that flag **disabled**.

## New research implementation

`src/assistx/owner_gateway_provenance.py` adds an optional `SignedGatewayOwnerMiddleware` to `assistx.api_router:app`. It is installed **only** when explicitly configured with `ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY=true` *and* `ASSISTX_REQUIRE_OWNER_BINDING=true`; otherwise current production behavior remains unchanged. Startup in enabled mode fails closed unless a pinned Ed25519 verification key and a private nonce-store directory are configured. No signing private key is present in this repository or required by the API.

For `POST /api/tasks`, the isolated research gateway must send one `X-AssistX-Signed-Owner` attestation: canonical sorted JSON bytes and a 64-byte Ed25519 signature, both encoded in strict base64url. The signed, bounded claim includes schema and audience, canonical issuer and user subject, exact method and path, SHA-256 of the bounded HTTP request body, issued/expiry seconds (maximum 45-second lifetime), and a 32-byte nonce. Signed identity becomes the request's `verified_owner` **only after signature, timestamp, request binding and one-use nonce verification**. Existing `authenticated_owner` still requires the owner subject to match the independently authenticated API user. Missing, duplicate, altered, expired, forged or replayed assertions fail closed; inconsistent authenticated users receive existing task-create denial. The nonce DB is private and transactionally unique, but **SQLite snapshot rollback can restore consumed nonces**; independent rollback resistance and issuer administration remain separate blockers.

These are two different trust steps: a future **independently authenticated gateway** is responsible for verifying the human/tenant principal **before signing**; the API verifies the pinned gateway signature and constrains its use to the precise request. A signature alone does not prove upstream user authentication. This implementation does not enroll or operate the external signing gateway.

## Tests and production preflight

New tests exercise the real FastAPI ASGI middleware with ephemeral synthetic Ed25519 keys and temporary local private nonce DBs, positive owner propagation, stale/forged/bad-audience/bad-body assertion rejection, wrong human vs authenticated API user, duplicate headers, unavailable key, replay after request and DB reopen, and an explicit whole-DB rollback *negative* demonstration. No real user, protected corpus, production Neo4j writes, or live task execution is used.

The existing opt-in read-only preflight now reports `signed_owner_gateway_enabled`, `signed_gateway_key_path_configured` and `signed_gateway_nonce_path_configured` as **booleans only**, never environment values. A live x1-370 preflight confirmed the current `assistx-api` runs `assistx.api_router:app` and exposes the five expected routes, but production `ASSISTX_REQUIRE_OWNER_BINDING` is not enabled, the signed-gateway module is not configured, and neither a gateway verification key path nor a nonce DB path is configured. **Deployment authorization stays false even when synthetic configuration fields are present.**

## Additional gates before promotion

Independent signed-attestation issuer, independent key custody and rotation, a gateway-side test proving actual authenticated principal before signing, nonce durability outside same-admin rollback, live two-owner isolation, concurrent revoked-claim and network-partition fencing, owner-bound signed receipts, and correct Basic/proxy-user auth binding are not yet proven. All production Hermes mounts (16 bind, nine RW) also require an approved signed RO knowledge source and independent writable scratch cutover, plus exact signed release image/rollback qualification.

**Keep PR #425 DRAFT, issue #414 OPEN, and running Hermes / AssistX unchanged.**
