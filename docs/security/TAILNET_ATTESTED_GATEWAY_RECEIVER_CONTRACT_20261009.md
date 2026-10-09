# Attested Tailnet identity — receiver-side mock contract

**Status: default-off, unwired research-only.** Date: October 9, 2026.
Blockers: [#149 ingress provenance](https://github.com/scottjoyner/auto-assist/issues/149),
[#156 public credential custody](https://github.com/scottjoyner/auto-assist/issues/156),
[#148 trace capacity](https://github.com/scottjoyner/auto-assist/issues/148).

## Why a proof is needed

The current `kipnerter-ios` main source `HermesAgentClient.applyAuthentication`
deliberately sends **no Authorization header** in `.fleetAuto` mode. Its route
depends on Tailscale Serve authenticating the phone and injecting
`Tailscale-User-Login`. Legacy iOS `.legacyDirect` sends an optional
**Bearer**, not Basic. Simply enabling AssistX's tested, default-off
`ASSISTX_REQUIRE_BASIC_AUTH` on the backend therefore blocks Agent Auto.

Actual x1-370 backend also has shared Docker bridge peers that can connect
directly to port 8000 without Tailscale Serve. A same-bridge disposable HTTP
negative test in [PR #202](https://github.com/scottjoyner/auto-assist/pull/202)
proved forged `Tailscale-User-Login` passed legacy trusted-header auth,
while strict Basic denied it. Neither behavior alone permits a safe, transparent
iOS migration.

## Smallest candidate architectural boundary

1. **Ingress**: Tailscale Serve authenticates the client, strips any user
   supplied identity headers, injects Tailnet identity. No direct Docker peer
   can reach the isolated signer/Serve backend path; verify physically.
2. **Signer**: a separately privileged gateway, accessible only from this
   authenticated ingress, signs a short-lived per-request assertion **after**
   independent identity validation. The signing key must never ship to iOS,
   live in Git, or reside on untrusted Docker peers.
3. **Receiver**: AssistX backend rejects raw forwarded identity as authority.
   It verifies the exact pinned Ed25519 key ID/issuer/audience, canonical
   message/signature, per-user subject, strict method + exact raw target
   including query, scope, bounded time and a fresh nonce.
4. **Replay custody**: the backend *atomically* consumes nonces in its own
   durable shared replay store, failing closed on Redis outage, generation loss,
   network partition and replay. An in-memory set or caller-provided
   `lambda: True` is NOT a production substitute.
5. **Authorization**: a verified principal is not automatically a permitted
   operator. A separate receiver-owned role/scope policy must authorize each
   backend operation; only then may route execution/Neo4j access proceed.

`src/assistx/trusted_gateway_identity_contract.py` is only the pure receiver
**verification contract**, not a gateway, signer, replay-store implementation,
FastAPI dependency or deployment. It cannot accept untrusted headers and
cannot construct a production signing key. The synthetic tests use freshly
generated Ed25519 keys and a fake atomic nonce consumer to prove fail-closed
parsing, incorrect/missing signatures, wrong audience, key, method, target,
scope, timestamps, noncanonical JSON, duplicate fields, replay and store
outage. It is deliberately not imported by the deployed app.

## Risks requiring independent acceptance

- Key provisioning, custody, rotation, signing-process isolation and post-#156
  historical credential cleanup are not done.
- The gateway signing service must not trust identity headers from Docker peers.
  No transport/IP/header convention can replace the authenticated Serve edge.
- The replay store needs durable anti-rollback *generation* authority, not an
  ephemeral Redis process ID or a Redis Lua key that disappears after restart.
- Claims must preserve per-user quota and role identity; a single shared Basic
  account would collapse operator identities.
- Correct handling of HTTP raw target bytes, percent encoding, query strings,
  clock skew, duplicated HTTP headers and sign/forward races needs physical
  negative tests on a real proxy and backend under isolated staging.
- iOS Agent Auto, trace UI/browser, legacy direct and other clients need real
  device/CI acceptance and an operator-approved rollback path.

**Never wire this module to live routes or deploy a signer until those gates
are independently proven. Keep production NO-GO.**
