# AssistX internal router alias migration — held preflight

**November 4, 2026 · x1-370 · research-only implementation**

The running AssistX API was traced to its source Compose inputs
under /home/scott/git/auto-assist-deploy-20261016. The live
git-router-1 container publishes TCP 8088 on wildcard IPv4
and IPv6 interfaces, with five administrative GET routes still
anonymously accessible in the stale October 7 image. Changing
the router's host publication without first checking actual AssistX
traffic can cause an outage.

## Physically verified runtime dependencies

A small read-only Python process was executed in assistx-api
without accessing or printing any credentials.

| Runtime setting | Actual configured destination | TCP test |
| --- | --- | --- |
| AUTO_ROUTER_BASE_URL | host.docker.internal:8088 | Reachable |
| FLEET_ROUTER_URL | router:8088 | Not reachable |
| Router network alias | llm-router:8088 | Reachable |

Inside the same running AssistX API, a status-only public
GET /health returned HTTP **200** via both host.docker.internal
and llm-router. The live router advertises llm-router on
git_default; AssistX API is also attached to git_default.
These checks do not prove authorized inference, provider
inventory, runtime projection, agent polling or every other
fleet client's compatibility.

The router admin token was observed as configured in AssistX,
but no token value was retrieved, printed or used. Existing
auto-assist control-room source explicitly requires
AUTO_ROUTER_ADMIN_TOKEN before requesting protected
/admin/providers and /admin/admission, and tests reject
credential forwarding on HTTP redirects.

## Actual implementation in this branch

The canonical docker-compose.yml defaults were corrected from
the nonexistent auto-router and router service names to:

- AUTO_ROUTER_BASE_URL default: http://llm-router:8088
- FLEET_ROUTER_URL default: http://llm-router:8088

The matching network-attachment comment was also updated.
The separate OPENAI_BASE_URL default is intentionally left
on the host LM Studio endpoint :1234 and must not be replaced
by the router URL as part of this narrow change.

The new host-side script scripts/audit_router_alias_preflight.py
issues only bounded read-only Docker inspection and a fixed
process inside the running AssistX API to check two URL settings,
DNS/TCP and HTTP status of public /health via the internal
alias. It never reads operator tokens, prints configured URL
values, issues admin requests, reads HTTP bodies, modifies
service configuration or touches Docker lifecycle/network commands.

It requires the patched source defaults, both runtime
settings to be exactly the canonical internal alias URL, and
stable container image/start identity across the check.
Its result is strictly PASS_ALIAS_PATH_PREFLIGHT_ONLY or
HOLD; even a PASS never authorizes restarting or deploying
a container.

## Real fail-closed preflight

The actual current runtime yielded:

- Source Compose defaults pinned: **PASS** (isolated checkout)
- llm-router DNS: **PASS**
- llm-router:8088 TCP: **PASS**
- llm-router:8088/health HTTP 200: **PASS**
- Both runtime router URLs use llm-router: **FAIL**

Therefore the preflight correctly returned **HOLD / exit 2**.

Private 0600 evidence receipt on x1-370:
/tmp/assistx-router-alias-20261104-1512.json
SHA-256:
22000372bcc788a1042849d4b15d0b4b11b8dbafa41043ef56d886ead5d365d9.

The script and Compose change passed 23 isolated synthetic tests
with --noconftest (the repository-wide test fixture requires
optional AssistX dependencies absent from the lightweight
test venv). Targeted Ruff F401/F821 lint passed.

## Cutover ordering — not yet authorized

1. Independently review/merge the minimal Compose + preflight
   correction. Do not assume changed defaults override an
   existing .env or Compose override value.
2. Review live Compose interpolation and reconcile only the two
   relevant URL environment overrides to llm-router:8088,
   without printing or changing any credential.
3. Approve a controlled AssistX-only change with state/traffic
   rollback. Do not change router host-port binding in the same
   step. After the controlled change, rerun the read-only preflight
   to verify actual runtime values and internal health.
4. Verify authenticated /admin/providers and /admin/admission
   compatibility plus legitimate inference, fleet executor and
   task polling paths without leaking tokens or response bodies.
5. Only after all router clients, external ingress, and a secure
   fallback or isolation option are independently validated,
   consider restricting port 8088 or replacing the hardened
   Auto-Router production image.

**This branch was not deployed. Both running AssistX URL settings
remain misaligned and the production Auto-Router admin exposure
remains live. The combined release gate is NO-GO.**

## Third router consumer: Hermes adapter — isolated relay is required

A source audit found that the Hermes adapter still defaults to
`HERMES_LM_BASE_URL=http://host.docker.internal:8088/v1`. It uses the
router's host-published port independently of both AssistX API URL settings.
Thus the existing two-variable migration **cannot** safely authorize removal
of host port 8088: Hermes may lose inference access.

An initial draft tried adding `hermes-adapter` to the shared
`git_default` bridge and redirecting its default URL to
`http://llm-router:8088/v1`. Hosted CI **correctly rejected this** via
`make release-verify-compose` with `hermes-adapter: network is not isolated`.
That proposal was reverted; **the enforced isolation policy remains intact**.

The preflight now reads only the existence/canonicality of Hermes's runtime
`HERMES_LM_BASE_URL`, its stable container identity, and a credential-free
DNS/TCP/public `GET /health` check against the router's internal alias.
It neither prints the URL nor reads credentials, HTTP bodies or private
API endpoints. `hermes_scoped_private_relay_reviewed=false` is a hard,
unconditional HOLD gate. A green API-only URL preflight is therefore
**insufficient** to close the router's public port.

**Implementation dependency:** design and independently review a scoped,
authenticated private `/v1` relay that preserves Hermes's existing isolated
Docker network, then verify exact-client inference, provider claims,
request identity, throughput, retry and error behavior, and token custody.
The relay must not forward arbitrary admin paths or grants and must not
change live service network membership before owner-approved staging.
Only after this separate implementation and independent tests pass may a
future PR explicitly relax the Hermes relay gate. Do not change the Compose
security verifier or attach privileged Hermes to a shared router bridge.

The running router and Hermes adapter have not been changed. No production
cutover, restart, or host firewall change is authorized; **NO-GO** remains.

## Disabled-by-default scoped relay implementation (next iteration)

A first **working research implementation**, not just a blocking preflight,
now lives at `src/assistx/hermes_router_relay.py`. The AssistX API mounts
its router **only** if `ASSISTX_HERMES_ROUTER_RELAY_ENABLED=true` is
explicitly set before startup. Otherwise the endpoint is absent from the
live FastAPI route table. It forwards **only**
`POST /api/internal/hermes-router/v1/chat/completions` to the constant
`http://llm-router:8088/v1/chat/completions`, from AssistX's previously
approved `git_default` attachment. Hermes remains on its existing isolated
network and would reach the AssistX API over the already available default
network when separately authorized. No Docker or firewall changes occur in
this PR.

Admission requires all three independently configured runtime values:
`ASSISTX_HERMES_RELAY_CLIENT_TOKEN` (a dedicated >=32 character client
bearer, distinct from the router token), `ASSISTX_INTERNAL_SERVICE_TOKEN`
(the upstream service token) and `ASSISTX_HERMES_RELAY_ALLOWED_MODELS`
(an explicit comma-separated list of approved model identifiers). No missing
token or model list falls back to a broad default. The relay only supports
bounded **text-only** OpenAI-compatible chat requests at this stage.
It limits requests to 64 KiB and 32 messages, up to 4,096 output tokens,
up to two concurrent upstream calls and 8 MiB of response data.
Unreviewed tool-call, multimodal and arbitrary proxy behavior is rejected.
Its HTTP transport does not inherit environment proxies, does not follow
redirects, does not propagate client cookies or admin headers, and issues
only one fixed upstream path with a separate upstream credential.
Mock-only negative tests cover disabled mode, bad auth, unwanted headers,
unapproved models and fields, duplicate JSON, oversize, redirect, media
type and upstream failures. Response streaming is bounded and cleaned up.

**Do not enable this on the current public AssistX instance yet.** The
actual operator-controlled Compose override must inject a separate Hermes
relay client credential (never the router service token), explicitly change
Hermes's `HERMES_LM_BASE_URL` to
`http://assistx-api:8000/api/internal/hermes-router/v1` and the Hermes
inference key to that dedicated credential. Existing operational Compose
defaults have **not** been changed by this iteration; legacy Hermes
routing remains unchanged in production. The API ingress layer must block
the internal relay path from public access independently of application
authentication; source review alone does not prove such a rule exists.
Staging must show a real Hermes client can generate tokens and stream,
with correct claim scopes, cancellation/backpressure, model/tool coverage,
limit enforcement and rollback. Tool calling is intentionally unsupported
in this initial scope; Hermes may require it before cutover. The
`hermes_scoped_private_relay_reviewed` preflight remains hard-false until
all these requirements are independently verified and approved.

## Upstream response integrity and cleanup (November 5 iteration)

The opt-in relay's **non-streaming** path now buffers at most 8 MiB and
validates a complete unique-key JSON object with a `choices` array
**before** returning 200 to Hermes. Upstream oversized Content-Length,
truncated/malformed JSON and duplicate JSON keys yield sanitized 502,
rather than an apparently successful partial response. Request error and
cancellation paths close their upstream client and release the bounded
admission semaphore; streaming ownership transfers only after upstream
status, content-type and size-header checks. Streamed output remains
bounded by 8 MiB but cannot retroactively change an already-emitted
HTTP 200 on a mid-stream failure; a separately witnessed live cancellation
and network-disconnect canary remains mandatory before enabling the relay.

This change does not alter the production container, Docker network,
credentials, ingress, model allowlist or draft NO-GO decision.

## Stream completion integrity (next fleet iteration)

The opt-in Hermes text-chat relay now validates bounded SSE frames and their
OpenAI-style `data: {...}` JSON objects (with a `choices` array). It
requires an upstream `data: [DONE]` final event before the relay ever
forwards a successful terminal marker. It handles chunk-split LF/CRLF frames,
caps the total stream at 8 MiB and individual frames at 256 KiB, rejects
duplicate JSON fields, unknown event fields, malformed response JSON and
trailing post-terminal data. A malformed first event yields a sanitized
502 **before HTTP 200**. Later malformed/truncated streams produce a
bounded `event: error` record with `relay_stream_incomplete` and never
fabricate `[DONE]`. Upstream client, response and concurrency slot are
released on completion, error or cancellation.

**Limitations:** HTTP 200 cannot be rolled back after the first streamed
model event; clients must treat the terminal `[DONE]` as completion and
interpret the `event: error` record as failure. Some custom OpenAI-like
backends emit nonstandard SSE fields/comment-only frames and may be
rejected by this strict research-only relay; real Hermes compatibility
and cancellation acceptance must be witnessed before a staging opt-in.
This does not add tool calling, public ingress access or any live network
change. The existing hard-false Hermes scoped relay gate remains HOLD.
