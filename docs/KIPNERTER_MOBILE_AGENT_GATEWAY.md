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

## HTTP contract

### `GET /api/v1/auth/whoami`

When the request arrived through Tailscale Serve and AssistX is configured with `TRUSTED_AUTH_HEADER=Tailscale-User-Login`, the response is:

```json
{
  "authenticated": true,
  "provider": "tailscale",
  "login": "user@example.com",
  "display_name": "Example User",
  "account_id": "tailscale:<stable-pseudonymous-id>",
  "session_expires_at": null
}
```

A request authenticated only with legacy Basic auth is deliberately not represented as Tailnet SSO.

### `POST /api/v1/agent/chat/completions`

Request shape is intentionally OpenAI-chat compatible so Kipnerter can retain its existing SSE parser:

```json
{
  "model": "hermes-agent",
  "stream": true,
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ]
}
```

`hermes-agent`, `auto`, `agent:auto`, and `fleet-auto` are routing aliases, not model identifiers. AssistX/Hermes chooses the actual fleet model. A client model override is ignored unless `KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE=1` is explicitly enabled on the server.

The response is `text/event-stream` with OpenAI-compatible `choices[].delta.content` records followed by `[DONE]`. The current bridge invokes the existing synchronous Hermes adapter and emits the completed result in bounded chunks; it does not claim token-by-token Hermes execution.

## Security boundary

Tailscale Serve removes incoming `Tailscale-User-*` headers and injects authenticated identity headers for tailnet user traffic. A backend that trusts those headers **must listen on localhost only**. The production AssistX environment therefore needs:

```dotenv
ASSISTX_API_BIND=127.0.0.1
ASSISTX_API_PORT=8000
TRUSTED_AUTH_HEADER=Tailscale-User-Login
KIPNERTER_TAILNET_ALLOWED_LOGINS=user@example.com
KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE=0
```

A non-secret overlay with these settings is maintained at `.env.kipnerter-gateway.example`. `KIPNERTER_TAILNET_ALLOWED_LOGINS` is optional but recommended. Tailnet ACL/grants remain the network authorization boundary; the login allowlist adds an application boundary for this mobile surface.

Do not use Tailscale Funnel for this endpoint.

### Serve is route-scoped, not an AssistX front door

The identity-bearing Serve listener does **not** proxy the entire AssistX API. It publishes only:

- `/health`
- `/api/v1/auth/whoami`
- `/api/v1/agent/chat/completions`

This matters because `TRUSTED_AUTH_HEADER` is part of the existing AssistX authentication dependency. A root proxy would unnecessarily put unrelated AssistX routes behind the same Tailnet identity boundary. The gateway verifier therefore fails if the old whole-API root mapping to port 8000 remains, and it probes `/api/degraded/status` to prove that a representative non-mobile AssistX route is not mounted through Serve.

### Legacy x1-370 Caddy path

x1-370 also has a pre-existing Caddy listener on `:8443` with an `/assistx/*` route that reaches `assistx-api:8000` over Docker networking. Host-loopback binding does not remove that container-to-container route.

Before enabling trusted-header auth in production, deploy the paired `scottjoyner/Sophia#13` change that strips these headers from the legacy Caddy upstream request:

- `Tailscale-User-Login`
- `Tailscale-User-Name`
- `Tailscale-User-Profile-Pic`

That preserves the legacy Caddy path and its normal AssistX authentication behavior without allowing an arbitrary `:8443` client to present itself as a Tailscale-authenticated Kipnerter user. The Kipnerter SSO authority remains Tailscale Serve only.

The deployment helper fails closed until the operator explicitly supplies `KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED=1`. Set that confirmation only after the paired Caddy change has actually been deployed/reloaded and verified on x1-370; merely having the PR open or merged is not enough.

## Exact-source deployment

The preferred deployment path is `scripts/deploy-kipnerter-tailnet-gateway.sh`. It deliberately mutates only the gateway-related keys in the selected AssistX environment file and leaves the existing database, router, executor, and other secret configuration in place.

The helper:

1. refuses a mismatched source checkout when `KIPNERTER_GATEWAY_SOURCE_SHA` is supplied;
2. refuses a dirty working tree by default;
3. refuses to enable trusted-header auth until `KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED=1` is supplied after the legacy Caddy header fence is deployed/reloaded;
4. creates a timestamped backup of the selected `.env` file before changing it;
5. forces the host-published AssistX API port to `127.0.0.1`;
6. enables `TRUSTED_AUTH_HEADER=Tailscale-User-Login`;
7. keeps mobile model override disabled;
8. validates Docker Compose configuration;
9. recreates only the AssistX API service (dependencies may start but are not force-recreated);
10. waits for loopback `/health`;
11. removes the obsolete whole-API Serve root only when it points to this exact AssistX loopback port;
12. publishes the three route-scoped Serve mounts; and
13. runs the gateway verifier.

If an existing Tailscale Serve root belongs to another service, the helper refuses to overwrite it.

From the exact backend candidate checkout, **after** deploying/reloading and verifying the Caddy fence:

```bash
KIPNERTER_GATEWAY_SOURCE_SHA="$(git rev-parse HEAD)" \
KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED=1 \
KIPNERTER_TAILNET_ALLOWED_LOGINS="user@example.com" \
KIPNERTER_GATEWAY_IDENTITY_PROBE=0 \
KIPNERTER_GATEWAY_AGENT_SMOKE=0 \
bash scripts/deploy-kipnerter-tailnet-gateway.sh
```

The deploy helper intentionally defaults the identity and Hermes smoke probes off. That permits server transport setup from the deployment host without pretending that the physical-iPhone release gate was exercised.

For a host-side Tailnet verification after Serve is configured:

```bash
TRUSTED_AUTH_HEADER=Tailscale-User-Login \
KIPNERTER_GATEWAY_IDENTITY_PROBE=1 \
KIPNERTER_GATEWAY_AGENT_SMOKE=1 \
bash scripts/verify-kipnerter-tailnet-gateway.sh
```

The verifier discovers the node's HTTPS Tailnet DNS name from `tailscale status --json`, checks the raw AssistX listener is loopback-only, requires the three Serve mounts, rejects the old AssistX root mapping, confirms a non-mobile AssistX route is not mounted, requires `whoami` to report an authenticated Tailscale identity when identity probing is enabled, and optionally requires a successful agent response carrying `X-Kipnerter-Agent-Executor: hermes`.

### Rollback

The deploy helper prints the exact environment backup path it created. If the deployment must be reverted, restore that backup and recreate the API service:

```bash
cp .env.pre-kipnerter-gateway.<timestamp> .env
docker compose --env-file .env up -d --build --force-recreate api
```

Remove only the Kipnerter Serve mount points if the rollout is abandoned. Do not use `tailscale serve reset` blindly on a host that may publish unrelated services.

If the legacy Caddy header fence was deployed as part of this rollout, it is safe to leave in place: stripping client-supplied Tailscale identity headers on a non-Serve proxy is the desired long-term behavior.

## Failure behavior

- Tailnet/DNS/TLS failure is reported by iOS separately from agent execution failure.
- A reachable `/health` endpoint is not sufficient. iOS probes `whoami` and the agent route.
- Hermes execution failures return 503 and do not silently masquerade as a healthy chat route.
- If the fleet agent route is unavailable, Kipnerter may use an explicitly selected direct LM Studio runtime as a fallback; this does not grant the phone an executor token.
- A failed submitted agent turn is not automatically replayed onto another route.

## Deployment acceptance

1. The legacy x1-370 Caddy AssistX proxy strips client-supplied Tailscale identity headers.
2. AssistX API host port is bound to `127.0.0.1`, not `0.0.0.0` or `::`.
3. Tailscale Serve is enabled, not Funnel, and exposes only the three approved Kipnerter paths.
4. `/api/degraded/status` and other non-mobile AssistX routes are not mounted through the Serve gateway.
5. An enrolled iPhone gets `authenticated=true` from `/api/v1/auth/whoami` without entering a second password.
6. The same user can complete a request through `/api/v1/agent/chat/completions` and the response identifies `X-Kipnerter-Agent-Executor: hermes`.
7. A direct LAN/tailnet request to raw port 8000 is impossible.
8. No `HERMES_EXECUTOR_TOKEN`, `ASSISTX_INTERNAL_SERVICE_TOKEN`, or Auto-Router admin token is present in the iOS app, its settings, or response payloads.
9. Kipnerter reports `Agent Auto` as the active route for the accepted turn.
10. The exact backend SHA, Caddy SHA, and exact iOS SHA used for acceptance are recorded before any RC2 TestFlight upload.
