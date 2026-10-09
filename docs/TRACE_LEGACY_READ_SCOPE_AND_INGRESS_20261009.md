# AssistX trace ingress — source mitigation and x1-370 inventory (2026-10-09)

**Status: SOURCE-ONLY DRAFT / PHYSICAL INGRESS #149 OPEN / GRAPH ADMISSION #148 OPEN.** No live host authentication, Tailscale Serve, Caddy, Redis, Neo4j or trace contents were changed or probed.

## Read-only current x1-370 network evidence

- `ss -lntp`: AssistX HTTP listener **127.0.0.1:8000**; separate landing-page HTTP listener **127.0.0.1:8081**; Tailscale Serve listener tailnet-only on **:8443** (IPv4 and IPv6). No wildcard host :8000 listener was observed in this snapshot.
- `docker ps --format`: running `assistx-api` publishes host `127.0.0.1:8000->8000/tcp`. This is evidence for one host, not all container-network, SSH-forwarded or Caddy ingress paths.
- `tailscale serve status`: port 8443 root `/` forwards to **127.0.0.1:8081**. Bounded paths `/health`, `/api/v1/auth/whoami`, `/api/v1/runtime/catalog`, `/api/v1/agent/chat/completions`, and `/api/v1/model/chat/completions` forward to **127.0.0.1:8000**. The root service was identified by its running `assistx-tailnet-home.service` PID and Python source at `/home/scott/git/assistx-tailnet-home/server.py`. Its source implements a static homepage and status endpoints, **not an AssistX API reverse proxy**. The root mapping is therefore not evidence of blanket AssistX API exposure *via that service*, although a physical network negative is not yet established.
- Source inventory `/home/scott/git/Sophia/voice-agent/caddy/Caddyfile` contains `handle @assistx` and `reverse_proxy assistx-api:8000` for `/assistx/*`. **Neither an active deployment nor identity-header sanitization has been proven** for this checkout. Do not mark legacy Caddy fence confirmed from source files alone.
- Tailscale Serve reports **other Funnel ports 8445/8446** forwarding to unrelated local services 8781/8123. The inventory did not establish that those destinations can reach AssistX or that they cannot. They remain explicit ingress-traversal tasks; do not probe user content.
- **Not inspected:** authentication values, basic password, Tailscale identity value, private JWT/signing keys, live trace payloads, production Caddy configuration, traffic captures, credentials, or actual header spoof probes.

## Source-level narrow mitigation stacked on paged trace client #179

Before the change, `api._auth_user_from_credentials` returned a nonempty configured trusted-header identity **before checking Basic**. The existing three legacy trace GET endpoints used that injected identity to open Neo4j. The old `/api/traces/{correlation_id}` returned full event payloads without paged limits.

In this draft:
- `GET /api/traces` and `GET /api/traces/{correlation_id}/evidence` require the injected auth **plus** separately verified Basic credentials matching the same principal via the existing metadata operator policy, *before* `_neo()`.
- `GET /api/traces/{correlation_id}` is **disabled by default** (`ASSISTX_LEGACY_TRACE_DETAIL_ENABLED` must be exactly `1`). Even when enabled, it additionally demands independent Basic validation and an explicit `ASSISTX_TRACE_PREVIEW_BASIC_USERS` membership via the existing preview policy, which defaults to an empty deny-all allowlist.
- Missing injected auth or policy hook => 503. Wrong Basic/mismatched user/broken policy => 403. Protected error paths specify `Cache-Control: no-store, private`. Feature gate and policy evaluate before `_neo()`.
- The paged trace UI in #179 has **no fallback** to legacy full-detail. Disabling that old endpoint therefore does not change the new client's network request shape.
- This is a **deliberate compatibility break** for header-only callers of the three old trace GET endpoints, and legacy detail callers without the explicit flag. It must be reviewed and deployed only after operator approval; do **not** turn on experimental paging or the legacy flag to bypass physical admission.

## Reproducible synthetic acceptance on x1-370

Isolated detached worktree: `/home/scott/git/.worktrees/assistx-trace-legacy-scope-20261009`

```sh
/tmp/assistx-dashboard-test-venv-20261009/bin/python -m pytest -q \
  tests/test_legacy_trace_read_scope.py tests/test_trace_detail_pages.py \
  tests/test_trace_context.py tests/test_trace_outcome_filter.py \
  tests/test_trace_task_evidence.py tests/test_trace_attestation.py
# 145 passed

node --test tests/test_trace_investigation_ui.cjs
# 30 passed
```

Tests simulate a forged client-provided header and check the **actual imported** `api.auth` priority using invented identities, no real auth values or network calls. Missing, mismatched or malformed Basic permission and broken authorizer fail before graph. Both flag and explicit allowlist are required to enable full legacy payload return.

## Release NO-GO items

1. Read-only source/proxy inventory is **not** end-to-end header stripping evidence. Physical isolated staging must prove forged, duplicated and mixed-case trusted headers are rejected at *every* real Caddy/Tailscale/forwarder/container ingress, with actual authenticated identity from the proxy, and no alternate route to host/container 8000. A signed/owned ingress review and rollback plan are still missing. **#149 OPEN.**
2. Basic username/password validation alone does not implement separate per-role privacy/RBAC authorization, tenant ownership, PII redaction or signed proxy identity. Legacy full detail still returns full payload when explicitly reenabled. Do not enable.
3. No globally durable remote physical Neo4j query admission or witnessed cancellation has been proven under Redis loss, worker crash or transport blackout. **#148 OPEN.** No production read workload, UI browser/device or system-scale driver p95/p99 test was performed.
4. The green mainline remediation stack ending in draft #199 is separate from the older paged-trace UI branch #179. This child must be reconciled against the accepted release base and pass exact-head full CI **without masking inherited failures**. No merge or promotion now.

**Disposition:** defend legacy paths in source while leaving staging and production activation blocked. Issue #176, #123 and retention #117 remain independently open.
