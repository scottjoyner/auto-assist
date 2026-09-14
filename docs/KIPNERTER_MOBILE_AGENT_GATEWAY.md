# Kipnerter mobile agent gateway

## Purpose

Kipnerter iOS must not know which fleet node runs Hermes, carry an Auto-Router executor token, or depend on a raw `host:port` Hermes endpoint. The normal mobile path is:

```text
iPhone
  -> HTTPS Tailscale Serve
  -> AssistX authenticated mobile boundary
  -> Hermes executor
  -> AssistX/Auto-Router scoped fleet runtime
```

The phone sends normal conversation context to AssistX. AssistX invokes Hermes server-side. Hermes and AssistX retain the claim-scoped/internal credentials used for model routing; those credentials are never returned to the phone.

## Live topology boundary

The approved topology keeps the AssistX API loopback-only and publishes only route-scoped mobile capabilities through the existing Tailnet-only HTTPS listener. Host standard TLS, unrelated Serve roots, and unrelated Funnel mappings must remain untouched.

Canonical mobile gateway URL currently used by the iOS configuration:

```text
https://x1-370.tailcb8954.ts.net:8443
```

This document defines the source/deployment contract. It is not, by itself, proof that the current PR head is deployed. Live deployment state must be re-verified before physical-device acceptance or release.

## HTTP contract

The mobile Serve surface consists of exactly four AssistX paths:

- `/health`
- `/api/v1/auth/whoami`
- `/api/v1/runtime/catalog`
- `/api/v1/agent/chat/completions`

An unrelated root service may coexist on the same Serve listener. The whole AssistX API must never be exposed as a Serve root, and `/api/degraded/status` must not be explicitly mounted to the mobile boundary.

### `GET /api/v1/auth/whoami`

When a request arrives through Tailscale Serve and AssistX is configured with `TRUSTED_AUTH_HEADER=Tailscale-User-Login`, the response reports authenticated Tailnet identity. A request authenticated only with legacy Basic auth is deliberately not represented to the app as Tailnet SSO.

### `GET /api/v1/runtime/catalog`

This is the authoritative mobile discovery projection used by Kipnerter's **Models & Agents** experience. It is derived from AssistX's approved runtime projection but intentionally returns only aggregate counts and opaque/coarse capabilities required by the phone.

It may return:

- schema/source and projection freshness timestamps;
- fleet runtime/model counts;
- agent-capable and code-capable runtime counts;
- opaque runtime IDs, runtime kind, model count, and coarse capability flags.

It must not return node names, raw runtime IDs, model IDs/provider model names, artifact fingerprints, access URLs, ports, or executor/router credentials. Catalog failure is observational only: it must not weaken Agent Auto authentication or cause the iPhone to infer hidden fleet topology.

### `POST /api/v1/agent/chat/completions`

The request is intentionally OpenAI-chat compatible so Kipnerter can retain its existing SSE parser. `hermes-agent`, `auto`, `agent:auto`, and `fleet-auto` are routing aliases, not model identifiers. AssistX/Hermes chooses the fleet model unless a server-side test override is explicitly enabled.

The bridge invokes the existing Hermes adapter and returns OpenAI-compatible response framing. Executor credentials remain server-side.

## Security boundary

Tailscale Serve strips client-supplied `Tailscale-User-*` identity headers and injects authenticated identity for tailnet traffic. A backend that trusts those headers must listen on localhost only. Production therefore requires:

```dotenv
ASSISTX_API_BIND=127.0.0.1
ASSISTX_API_PORT=8000
TRUSTED_AUTH_HEADER=Tailscale-User-Login
KIPNERTER_TAILNET_ALLOWED_LOGINS=user@example.com
KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE=0
```

`KIPNERTER_TAILNET_ALLOWED_LOGINS` is optional but recommended. Tailnet ACL/grants remain the network boundary; the application allowlist adds a second boundary for the mobile surface.

Do not use Funnel for this gateway. Do not add or rotate an Auto-Router admin credential. Do not place an AssistX/Hermes/Auto-Router executor credential on the iPhone.

### Legacy Caddy path

Any legacy Caddy AssistX proxy must strip client-authored Tailscale identity headers before trusted-header authentication is enabled. The required long-term fence removes:

- `Tailscale-User-Login`
- `Tailscale-User-Name`
- `Tailscale-User-Profile-Pic`

This prevents arbitrary legacy-proxy clients from impersonating Tailscale-authenticated Kipnerter users.

## Exact-source deployment

Use `scripts/deploy-kipnerter-tailnet-gateway.sh`. The helper must:

1. require the expected source SHA when supplied;
2. refuse a dirty checkout by default;
3. require explicit confirmation that the Caddy identity-header fence is live;
4. back up the selected `.env`;
5. keep AssistX host publication loopback-only;
6. enable the canonical Tailscale trusted identity header;
7. keep mobile model override disabled;
8. validate Docker Compose;
9. recreate only the AssistX API service;
10. wait for loopback health;
11. preserve unrelated Serve roots/Funnel state;
12. ensure only the four mobile path mounts listed above are published; and
13. run the structural gateway verifier.

If any exact mobile path is already owned by another Serve target, the helper must stop instead of overwriting it. It must never run `tailscale serve reset`.

The identity, catalog, and agent probes are acceptance checks. Host-side transport setup must not be mistaken for physical-iPhone acceptance.

## Verification

`scripts/verify-kipnerter-tailnet-gateway.sh` is the deployment verifier. It must prove:

- raw AssistX port 8000 remains loopback-only;
- the expected Tailnet HTTPS listener exists;
- all four mobile path mounts exist;
- unrelated root services may remain, but a whole-AssistX root mapping is rejected;
- `/api/degraded/status` is not explicitly mounted to AssistX;
- `whoami` reports authenticated Tailscale identity when identity probing is enabled;
- the runtime catalog returns HTTP 200 with the sanitized schema/counts and no forbidden routing/network detail when catalog probing is enabled;
- an optional bounded agent smoke returns HTTP 200 and `X-Kipnerter-Agent-Executor: hermes`.

A reachable `/health` route alone is not acceptance.

## Rollback

Restore the deployment helper's timestamped environment backup and recreate only the AssistX API if the backend configuration must be reverted.

If abandoning this mobile gateway, remove only these four Serve mounts from the selected Tailnet HTTPS listener:

- `/health`
- `/api/v1/auth/whoami`
- `/api/v1/runtime/catalog`
- `/api/v1/agent/chat/completions`

Do **not** use `tailscale serve reset`, because the node may publish unrelated services. The Caddy identity-header fence should normally remain in place.

## Failure behavior

- Tailnet/DNS/TLS failure is separate from agent execution failure.
- A reachable gateway is not sufficient; Agent Auto must prove the mobile agent contract and authenticated identity.
- Runtime-catalog failure must remain a discovery/observability failure, not an authentication bypass.
- Hermes execution failures return an application error rather than masquerading as a healthy chat route.
- Direct LM Studio remains an explicit bounded fallback lane and does not grant the phone an executor token.
- A failed submitted agent turn is not automatically replayed to another route.

## Deployment acceptance

Before physical-iPhone acceptance or an RC/TestFlight decision, record the exact backend, gateway/fence, and iOS source SHAs and prove the live deployment at those identities. Acceptance requires:

1. the legacy proxy cannot spoof Tailscale identity;
2. AssistX port 8000 is loopback-only;
3. existing standard TLS, unrelated Serve root, and unrelated Funnel state remain intact;
4. exactly the four approved AssistX mobile paths are published;
5. the enrolled iPhone gets authenticated Tailnet identity without a second password/bearer token;
6. the runtime catalog is available and sanitized;
7. the same iPhone completes an Agent Auto request proving Hermes execution; and
8. no executor/admin credential appears in iOS settings, configuration, or response payloads.
