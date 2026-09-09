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

The phone sends the normal conversation context to AssistX. AssistX invokes Hermes server-side. Hermes and AssistX retain the claim-scoped/internal credentials used for model routing; those credentials are never returned to the phone.

## Live x1-370 topology

Runtime inspection on 2026-09-09 established the authoritative listener layout:

- host standard TLS `:443` is owned by the `x1-370-links` Caddy container;
- Tailscale Serve already owns tailnet-only HTTPS `:8443` with an existing Nextcloud root mount;
- an unrelated Funnel mapping exists on `:8445` for Sophia/voice and must remain untouched;
- AssistX host port `8000` is now bound to `127.0.0.1` only.

The Kipnerter mobile gateway therefore **does not claim :443**. It adds only three more-specific AssistX paths to the existing Tailscale Serve `:8443` listener while preserving the Nextcloud root and unrelated Funnel state.

Canonical mobile gateway URL:

```text
https://x1-370.tailcb8954.ts.net:8443
```

## HTTP contract

### `GET /api/v1/auth/whoami`

When a request arrives through Tailscale Serve and AssistX is configured with `TRUSTED_AUTH_HEADER=Tailscale-User-Login`, the response reports authenticated Tailnet identity. A request authenticated only with legacy Basic auth is deliberately not represented as Tailnet SSO.

### `POST /api/v1/agent/chat/completions`

The request is intentionally OpenAI-chat compatible so Kipnerter can retain its existing SSE parser. `hermes-agent`, `auto`, `agent:auto`, and `fleet-auto` are routing aliases, not model identifiers. AssistX/Hermes chooses the fleet model unless a server-side test override is explicitly enabled.

The current bridge invokes the existing synchronous Hermes adapter and returns OpenAI-compatible response framing; it does not claim token-by-token Hermes execution.

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

Do not use Funnel for this gateway.

### Serve is route-scoped, not an AssistX front door

Only these AssistX routes are added to Tailscale Serve:

- `/health`
- `/api/v1/auth/whoami`
- `/api/v1/agent/chat/completions`

An unrelated root mount such as the existing Nextcloud `/` on `:8443` is allowed and preserved. A root mount that proxies the whole AssistX backend is forbidden. `/api/degraded/status` must not be explicitly mounted to AssistX through the mobile Serve boundary.

### Legacy Caddy path

The host Caddy listener on standard `:443` also provides a legacy `/assistx/*` proxy into Docker networking. Host-loopback binding alone does not eliminate that container-to-container path.

Before enabling trusted-header auth, the paired `scottjoyner/Sophia#13` fence must be deployed/reloaded so the legacy Caddy upstream removes:

- `Tailscale-User-Login`
- `Tailscale-User-Name`
- `Tailscale-User-Profile-Pic`

That runtime fence was deployed and validated on x1-370 on 2026-09-09. It prevents arbitrary legacy-proxy clients from impersonating Tailscale-authenticated Kipnerter users.

## Exact-source deployment

Use `scripts/deploy-kipnerter-tailnet-gateway.sh`. It:

1. requires the expected source SHA when supplied;
2. refuses a dirty checkout by default;
3. requires explicit confirmation that the Caddy identity-header fence is live;
4. backs up the selected `.env`;
5. forces AssistX host publication to `127.0.0.1`;
6. enables `TRUSTED_AUTH_HEADER=Tailscale-User-Login`;
7. keeps mobile model override disabled;
8. validates Docker Compose;
9. recreates only the AssistX API service;
10. waits for loopback health;
11. preserves unrelated Serve roots/Funnel state;
12. adds only the three mobile path mounts on `KIPNERTER_GATEWAY_SERVE_PORT` (default `8443`); and
13. runs the structural gateway verifier.

If any of the three exact mobile paths is already owned by another Serve target, the helper stops instead of overwriting it. It never runs `tailscale serve reset`.

Example:

```bash
KIPNERTER_GATEWAY_SOURCE_SHA="$(git rev-parse HEAD)" \
KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED=1 \
KIPNERTER_GATEWAY_SERVE_PORT=8443 \
KIPNERTER_TAILNET_ALLOWED_LOGINS="user@example.com" \
KIPNERTER_GATEWAY_IDENTITY_PROBE=0 \
KIPNERTER_GATEWAY_AGENT_SMOKE=0 \
bash scripts/deploy-kipnerter-tailnet-gateway.sh
```

The identity and agent probes default off during transport deployment so host-side setup cannot be mistaken for physical-iPhone acceptance.

## Verification

After publication:

```bash
TRUSTED_AUTH_HEADER=Tailscale-User-Login \
ASSISTX_API_PORT=8000 \
KIPNERTER_GATEWAY_SERVE_PORT=8443 \
KIPNERTER_GATEWAY_IDENTITY_PROBE=1 \
KIPNERTER_GATEWAY_AGENT_SMOKE=1 \
bash scripts/verify-kipnerter-tailnet-gateway.sh
```

The verifier:

- requires raw AssistX port 8000 to remain loopback-only;
- derives the node MagicDNS name from Tailscale state;
- targets `https://<node>.<tailnet>.ts.net:8443` by default;
- requires all three mobile path mounts;
- permits unrelated root services but rejects a whole-AssistX root mapping;
- rejects an explicit `/api/degraded/status` mobile mount;
- requires `whoami` to report `authenticated=true` with provider `tailscale` when identity probing is enabled;
- optionally requires an agent response containing `X-Kipnerter-Agent-Executor: hermes`.

## Rollback

The deployment helper prints its timestamped environment backup. Restore that backup and recreate the API if the backend configuration must be reverted.

If abandoning the mobile gateway, remove only these three Serve mounts from `:8443`:

- `/health`
- `/api/v1/auth/whoami`
- `/api/v1/agent/chat/completions`

Do **not** use `tailscale serve reset` on x1-370 because the node publishes unrelated services.

The deployed Caddy identity-header fence should normally remain in place; stripping client-authored Tailscale identity headers on a non-Serve proxy is the desired long-term behavior.

## Failure behavior

- Tailnet/DNS/TLS failure is separate from agent execution failure.
- A reachable `/health` endpoint is not sufficient; Agent Auto must prove `whoami` and the agent route.
- Hermes execution failures return an application error rather than masquerading as a healthy chat route.
- Direct LM Studio remains an explicit fallback lane and does not grant the phone an executor token.
- A failed submitted agent turn is not automatically replayed to another route.

## Deployment acceptance

1. The legacy Caddy AssistX proxy strips client-supplied Tailscale identity headers.
2. AssistX port 8000 is bound only to loopback.
3. Caddy retains host TLS `:443`.
4. Existing Tailscale Serve `:8443` root/Nextcloud and unrelated Funnel state remain intact.
5. Only the three approved AssistX mobile paths are added on Serve `:8443`.
6. `/api/degraded/status` is not explicitly mounted to AssistX through Serve.
7. An enrolled iPhone gets `authenticated=true` from `/api/v1/auth/whoami` without a second password or bearer token.
8. The same iPhone completes an Agent Auto request and the response identifies `X-Kipnerter-Agent-Executor: hermes`.
9. No Hermes executor, AssistX internal-service, or Auto-Router admin credential appears in iOS settings or response payloads.
10. Exact backend, Caddy, and iOS SHAs are recorded before any RC2 TestFlight upload.
