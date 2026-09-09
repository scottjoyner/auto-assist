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
TRUSTED_AUTH_HEADER=Tailscale-User-Login
KIPNERTER_TAILNET_ALLOWED_LOGINS=user@example.com
```

`KIPNERTER_TAILNET_ALLOWED_LOGINS` is optional but recommended. Tailnet ACL/grants remain the network authorization boundary; the login allowlist adds an application boundary for this mobile surface.

Do not use Tailscale Funnel for this endpoint.

After changing the environment, recreate the AssistX API container and run:

```bash
TRUSTED_AUTH_HEADER=Tailscale-User-Login ./scripts/configure-kipnerter-tailnet-serve.sh
```

The helper refuses to enable the identity gateway when it detects a wildcard listener for the AssistX host port.

## Failure behavior

- Tailnet/DNS/TLS failure is reported by iOS separately from agent execution failure.
- A reachable `/health` endpoint is not sufficient. iOS probes `whoami` and the agent route.
- Hermes execution failures return 503 and do not silently masquerade as a healthy chat route.
- If the fleet agent route is unavailable, Kipnerter may use an explicitly selected direct LM Studio runtime as a fallback; this does not grant the phone an executor token.

## Deployment acceptance

1. AssistX API host port is bound to `127.0.0.1`, not `0.0.0.0` or `::`.
2. Tailscale Serve is enabled, not Funnel.
3. An enrolled iPhone gets `authenticated=true` from `/api/v1/auth/whoami` without entering a second password.
4. The same user can complete a request through `/api/v1/agent/chat/completions` and the response identifies `X-Kipnerter-Agent-Executor: hermes`.
5. A direct LAN/tailnet request to raw port 8000 is impossible.
6. No `HERMES_EXECUTOR_TOKEN`, `ASSISTX_INTERNAL_SERVICE_TOKEN`, or Auto-Router admin token is present in the iOS app, its settings, or response payloads.
