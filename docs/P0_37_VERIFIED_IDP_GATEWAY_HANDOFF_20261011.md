# P0 #37 — Verified IdP-to-Auto-router gateway (October 11, 2026)

**Status:** experimental, DEFAULT OFF, DRAFT-ONLY, NOT DEPLOYED.

- Identity dependency: [AssistX issue #297](https://github.com/scottjoyner/auto-assist/issues/297)
- Router owner/receipt target: [Auto-router #37](https://github.com/scottjoyner/auto-router/issues/37)
- Real router delegation middleware: [draft PR #48](https://github.com/scottjoyner/auto-router/pull/48)
- Disposable shared PostgreSQL owner proof: [draft PR #51](https://github.com/scottjoyner/auto-router/pull/51)

## What the new route actually does

`src/assistx/p0_oidc_gateway.py` introduces a separate, **non-streaming,
bounded** route `POST /api/p0/verified-local-chat`, mounted from
`src/assistx/api.py` **only if**
`ASSISTX_P0_VERIFIED_HUMAN_GATEWAY_ENABLED=true` is explicitly set.
Otherwise it is **absent**, not a permissive fallback.

1. Verify a signed **RS256 IdP JWT** using a private, pinned, offline JWKS
   (no untrusted `jku`, dynamic key retrieval or token introspection).
   Require issuer, exact API audience, registered signing key ID, signed
   stable `sub`, bounded issuance/expiry and canonical Base64URL.
   A roster controlled by the gateway operator maps that issuer/`sub` to
   a human tenant+subject and approved model handles. Neither caller
   `user`, metadata, trusted-user header nor Basic account supplies
   tenant/subject.
2. Restrict request body to `model`, `messages`, and `max_tokens`.
   This initial canary is **nonstreaming only** (max 16 messages,
   max 4096 characters per message, max 64 output tokens, total HTTP
   body max 65536 bytes). Never accept user-supplied router service bearer,
   delegation, idempotency, routing preference or traceowner fields.
3. Mint an Ed25519 delegation conforming to Auto-router PR #48:
   `iss=trusted-user-gateway-p0`, `aud=auto-router-p0-shadow-user`,
   pinned key ID, signed `tenant`, `subject`, exact authorized model,
   UUIDv4 root and attempt, unique JTI, 60-second TTL.
4. Forward **server-to-server only**, over pinned HTTPS
   `/v1/chat/completions`, using a separate private router-service bearer
   and the freshly generated delegation header; do not follow redirects,
   auto-load models or silently switch providers. No token or signing key
   is returned to the client.
5. Forward the bounded router JSON response/status with no downstream
   cookies, service secrets or headers. A router 503 remains a 503; an
   invalid IdP credential remains rejected without dispatch.

No provider, trace store, speaker profile, production IdP, live router
configuration or user account was accessed to implement this slice.

## Required pinning at future approved deployment

These flags are **documented but NOT SET** in any live environment:

| Name | Intent |
| --- | --- |
| `ASSISTX_P0_VERIFIED_HUMAN_GATEWAY_ENABLED` | Default absent/false |
| `ASSISTX_P0_IDP_JWKS_FILE` | Absolute operator-owned private pinned keyset JSON |
| `ASSISTX_P0_HUMAN_ROSTER_FILE` | Private allowlist with pinned HTTPS issuer, IdP sub → tenant/subject and allowed models; `kind=human` |
| `ASSISTX_P0_ROUTER_SIGNING_KEY_FILE` | Separate Ed25519 **private** key, file mode 0600 and same UID |
| `ASSISTX_P0_ROUTER_SIGNING_KID` | Must match Auto-router verifier's pinned key ID |
| `ASSISTX_P0_ROUTER_SERVICE_BEARER_FILE` | Separate private service bearer, never exposed to client |
| `ASSISTX_P0_ROUTER_URL` | Pinned `https://.../v1/chat/completions`; redirects forbidden |
| `ASSISTX_P0_IDP_AUDIENCE` | An API-specific IdP audience; cannot accept a generic client-app ID token |

**Identity caveat:** a cryptographically valid fixture IdP token is not
proof of two *real* independently authenticated human sessions. Before
production, establish actual IdP accounts/login state, access-token purpose
and audience, revocation, session requirements, JWKS rotation, issuer
subject-kind governance and an authenticated protected gateway-to-router
network boundary. This prototype uses static pinned keys/allowlists and
has not been evaluated against a live IdP.

## Security and test evidence

Isolated `tests/test_p0_oidc_gateway.py` generates disposable 2048-bit
RSA IdP keys and Ed25519 gateway keys. It verifies:

- Two separately signed IdP `sub` identities mint distinct rooted user
  delegations with correct issuer/audience/model/TTL and valid Ed25519 signature.
- Wrong issuer, audience, time, signer, JWK kid, unrecognized subject
  or unapproved model cannot mint a delegated actor.
- Client-provided `user`, `metadata`, `stream`, `tenant` or oversized
  requests are rejected rather than forwarded as owner evidence.
- Forwarded outbound request has only gateway-generated service bearer,
  freshly signed delegated attempt and idempotency key. Caller-supplied
  trusted-header/impersonation fields are absent.
- A 503 downstream router is passed through rather than retried/failing
  over or relabeled as delivered.
- Default OFF adds no route; enabled with missing keys fails startup;
  a spoofed trusted-header request without a signed IdP bearer is rejected.

**Focused x1-370 evidence:** commit
`65828d55b12c1159411dd139f66b6d1da6476f10`,
Ruff PASS and **14 passed** in 0.59 seconds using a lightweight isolated
pytest run. Repository-wide AssistX tests/hosted CI/real IdP integration
are **NOT** covered by this focused result.

## Remaining P0 release stops

- Persistent trusted human session owner/root issuance and retry policy
  across gateway restarts and key rotation, plus protected IdP revocation.
- Auto-router shared PostgreSQL *single-primary fencing*, rollback/epoch
  verification and authorized deployment; two active primaries are unsafe.
- Independently witnessed actual provider-process/attempt execution, not
  merely a forwarded HTTP 200 or SSE completion.
- Approved encrypted producer/node/tenant/subject trace custody and
  verified per-actor read/subscription/offline replay.
- Real, distinct human A/B physical proof with exact-device/iPhone evidence.
- Physical Beelink currently **NO_GO** due root-capacity and high I/O
  pressure; a loaded model in its native inventory does not grant permission
  to run inference.

**No release, merge, production switch, or physical inference is authorized
by these synthetic gateway tests.**
