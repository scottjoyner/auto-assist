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

## Exact-source deployment

The preferred deployment path is `scripts/deploy-kipnerter-tailnet-gateway.sh`. It deliberately mutates only the gateway-related keys in the selected AssistX environment file and leaves the existing database, router, executor, and other secret configuration in place.

The helper:

1. refuses a mismatched source checkout when `KIPNERTER_GATEWAY_SOURCE_SHA` is supplied;
2. refuses a dirty working tree by default;
3. creates a timestamped backup of the selected `.env` file before changing it;
4. forces the host-published AssistX API port to `127.0.0.1`;
5. enables `TRUSTED_AUTH_HEADER=Tailscale-User-Login`;
6. keeps mobile model override disabled;
7. validates Docker Compose configuration;
8. recreates only the AssistX API service (dependencies may start but are not force-recreated);
9. waits for loopback `/health`;
10. configures Tailscale Serve using the guarded Serve helper; and
11. runs the gateway verifier.

From the exact backend candidate checkout:

```bash
KIPNERTER_GATEWAY_SOURCE_SHA="$(git rev-parse HEAD)" \
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

The verifier discovers the node's HTTPS Tailnet DNS name from `tailscale status --json`, checks the raw AssistX listener is loopback-only, checks the Serve mapping, requires `whoami` to report an authenticated Tailscale identity when identity probing is enabled, and optionally requires a successful agent response carrying `X-Kipnerter-Agent-Executor: hermes`.

### Rollback

The deploy helper prints the exact environment backup path it created. If the deployment must be reverted, restore that backup and recreate the API service:

```bash
cp .env.pre-kipnerter-gateway.<timestamp> .env
docker compose --env-file .env up -d --build --force-recreate api
```

If the old deployment did not use Serve, also remove the new Serve mapping using the installed Tailscale CLI after confirming which other Serve routes are present. Do not reset all Serve configuration blindly on a host that may publish unrelated services.

## Failure behavior

- Tailnet/DNS/TLS failure is reported by iOS separately from agent execution failure.
- A reachable `/health` endpoint is not sufficient. iOS probes `whoami` and the agent route.
- Hermes execution failures return 503 and do not silently masquerade as a healthy chat route.
- If the fleet agent route is unavailable, Kipnerter may use an explicitly selected direct LM Studio runtime as a fallback; this does not grant the phone an executor token.
- A failed submitted agent turn is not automatically replayed onto another route.

## Deployment acceptance

1. AssistX API host port is bound to `127.0.0.1`, not `0.0.0.0` or `::`.
2. Tailscale Serve is enabled, not Funnel.
3. An enrolled iPhone gets `authenticated=true` from `/api/v1/auth/whoami` without entering a second password.
4. The same user can complete a request through `/api/v1/agent/chat/completions` and the response identifies `X-Kipnerter-Agent-Executor: hermes`.
5. A direct LAN/tailnet request to raw port 8000 is impossible.
6. No `HERMES_EXECUTOR_TOKEN`, `ASSISTX_INTERNAL_SERVICE_TOKEN`, or Auto-Router admin token is present in the iOS app, its settings, or response payloads.
7. Kipnerter reports `Agent Auto` as the active route for the accepted turn.
8. The exact backend SHA and exact iOS SHA used for acceptance are recorded before any RC2 TestFlight upload.
