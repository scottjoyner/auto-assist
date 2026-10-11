# AssistX ↔ Fleet Model Link operator canary

**Status:** experimental, opt-in and disabled by default. October 10, 2026. This is an operator-controlled integration canary; **not** an authorized replacement for auto-router, Hermes, the existing Kipnerter gateway, the Neo4j/PostgreSQL admission model, or a model-promotion workflow.

## Why this boundary is necessary

The [Fleet Model Link gateway](https://github.com/scottjoyner/fleet-hardware/pull/27) and its [SSE/trace follow-on](https://github.com/scottjoyner/fleet-hardware/pull/29) let a local OpenAI-compatible client address a signed `node/alias` model through loopback and one verified mTLS peer hop. They do *not* embed or validate AssistX's `artifact_fingerprint` authority, runtime approval, capacity lease, privacy policy or bearer credentials.

Meanwhile `mobile_agent_routes.py` already gates Kipnerter's opaque `model:v1:<HMAC>` handle on a fresh AssistX signed runtime projection, and sends approved work through its existing router. **Never** infer artifact identity from a model alias. A live `lenovo/lfm` label is not proof that its weights match a previously admitted fingerprint.

This adapter adds a separate **operator-only** route under `/api/fleet-link`, behind AssistX's existing auth dependency **and** a default-deny operator identity allowlist plus independently held operator token, enabled *only* when a local operator sets explicit environment controls. Its only inference action is a single bounded manually requested canary; it cannot enter normal task dispatch or a phone release path. Disabling the feature prevents all outbound sidecar network calls.

## Exact admission intersection

A positive canary requires **all** of the following:

1. `ASSISTX_FLEET_LINK_CANARY=1` explicitly set by operator; caller must also match `ASSISTX_FLEET_LINK_OPERATOR_USERS` and provide a >=32-character independent token through `X-AssistX-Fleet-Operator-Token` matching `ASSISTX_FLEET_LINK_OPERATOR_TOKEN`. Missing or weak token, unset allowlist or mismatch => 403 before any sidecar request.
2. `ASSISTX_FLEET_LINK_URL` exactly `http://127.0.0.1:<non-privileged-port>` (literal IP only; no hostname/DNS, credentials, path, query, fragment or redirects), plus `ASSISTX_FLEET_LINK_RUNTIME_ID` pinned to the exact manually approved `RuntimeInstance.runtime_instance_id`.
3. A **fresh, independently approved AssistX runtime projection** from the existing `_current_runtime_projection()` authority, whose matching provider has `runtime_kind=type=openai_compatible`, exact pinned runtime ID, matching `base_url=<literal-loopback>/v1`, and a *single* exact approved AccessPath. No route is admitted from a stale/incomplete projection.
4. A model in that approved runtime with **nonempty artifact fingerprint**, `chat` and `local_only` capabilities, and an exact `node/alias` `provider_model` that is *not* an `auto/*` magic router alias. Its opaque HMAC-based mobile model handle is resolved exclusively from the signed projection.
5. Sidecar `GET /fleet/nodes` independently reports the exact model as `ready_now=true`, `status=READY_PROVED_RECENTLY`, `kind=chat`, and `GET /v1/models` independently lists the same exact model. Inventory alone is insufficient. Duplicate model handles that would select different physical instances are quarantined.
6. A single bounded nonstreaming user-only canary prompt (<=256 characters) with <=64 output tokens; no agent tools, system prompts, web access, model autoloading, fallback retries, replay or workflow dispatch.
7. The response model must match the exact selected route, return a single normally completed (`finish_reason=stop`) assistant text <=4096 characters, and when the caller passes a syntactically valid W3C `traceparent`, the sidecar must return the matching `X-Fleet-Trace-Id`.

Even when all checks pass, the canary's response is rewrapped with only the HMAC `model_handle`, the assistant text, and `canary_only=true` / `no_automatic_dispatch=true`. It does not return backend model IDs, node addresses, the artifact fingerprint, internal response metadata, or the AssistX router's bearer token. No prompt contents or identities are intentionally logged. The existing app's auth policy is unchanged.

## New endpoints

| Route | Mode | Notes |
|---|---|---|
| `GET /api/fleet-link/preview` | Operator-authenticated, observation-only | Sanitized admitted vs ready counts, no model aliases, fingerprint, or network coordinates. `NOT_VERIFIED` with zero ready models when feature disabled or evidence unavailable. |
| `POST /api/fleet-link/canary` | Operator-authenticated, one-shot shadow inference when explicitly enabled | `model_handle`, `prompt`, `max_tokens` only, nonstream. Unavailable or unapproved => sanitized 503. Never changes standard `/api/v1/model/chat/completions`, queues, mobile credentials, nor runtime projection. |

The endpoint is wired into `api_router.py` with AssistX's existing `auth` dependency and separate operator proof; **it is not** an additional unauthenticated mobile endpoint. It uses a freshly constructed no-proxy HTTPX client, disables redirects and has a 4s bounded overall timeout. Docker/container `localhost` is *not* automatically host localhost; a production deployment must prove the sidecar is reachable within the **same explicit network namespace** without opening the sidecar to a LAN.

### Explicit opt-in contract (do not set on live services yet)

```bash
ASSISTX_FLEET_LINK_CANARY=1
ASSISTX_FLEET_LINK_URL=http://127.0.0.1:19341
ASSISTX_FLEET_LINK_RUNTIME_ID=fleetlink:destroyer
KIPNERTER_MOBILE_MODEL_HANDLE_SECRET=<existing-private-secret>
ASSISTX_FLEET_LINK_OPERATOR_USERS=<explicit-authorized-operator-identity>
ASSISTX_FLEET_LINK_OPERATOR_TOKEN=<independent-random-secret-at-least-32-characters>
```

The authenticated caller must provide `X-AssistX-Fleet-Operator-Token: <same independent random secret>`. Never embed the operator token in an iPhone binary or check it into a repository; supply it only through a controlled operator workflow over protected transport. The token is **separate from** the existing AssistX Basic password and trusted identity header. **Defense in depth is not ingress verification:** AssistX `auth()` can accept `TRUSTED_AUTH_HEADER`. The deployed reverse proxy must demonstrably strip untrusted client values, inject only authenticated identities, and prevent direct untrusted access to ASGI.

`fleetlink:destroyer` is an **example** approved runtime ID, not evidence that this runtime exists in the production graph. Do not generate fake Graph `RuntimeInstance`, `LoadedModelInstance`, `ModelArtifact`, `AccessPath` or `CapacityObservation` approval records simply to make this preview green.

Before any enabled canary, the deployed sidecar itself must use a **locally pinned Ed25519-signed catalogue** and **loopback-only raw model servers**, with a real authorised access path. This adapter's `/fleet/nodes` observer output is **not independently cryptographically signed**. Its successful synthetic canary does *not* certify the sidecar binary/certificate, authoritative artifact hash or physical user path. Those must be independently verified at deployment.

### Reproducible tests

```bash
cd /home/scott/git/wt-assistx-fleet-link-canary-20261010
PYTHONPATH=src pytest -q tests/test_fleet_link_canary.py \
  tests/test_mobile_runtime_catalog.py tests/test_mobile_agent_routes.py \
  tests/test_runtime_projection.py tests/test_runtime_projection_v2.py \
  tests/test_router_integration.py
PYTHONPATH=src python3 scripts/fleet_link_joint_canary.py \
  --fleet-link-root /home/scott/git/wt-fleet-link-stream-trace-20261010
```

The joint acceptance script uses **actual Fleet Model Link source, ephemeral local HTTP model and sidecar servers, temporary Ed25519 catalog signing keys, and a disposable AssistX FastAPI route**. It proves `AssistX operator authorization → fresh approved projection → exact fingerprint-backed model handle → live sidecar status → one bounded localhost completion → trace ID echoed`. It sends no requests to live fleet endpoints and makes no configuration changes. In separate standalone regression tests, malformed projections, invalid URLs, expired approvals, inventory-only health, unauthorized callers and model mismatch fail closed.

## Existing release issue uncovered and fixed

During the targeted regression run, two existing mobile catalog tests failed because `mobile_agent_routes.py` imported `RuntimeProjectionSigningError` from `runtime_projection_v2.py`, which does not define that name. The runtime projection generator already signals its failed-signature/approval path via `runtime_projection.RuntimeProjectionBlocked`. This branch supplies a compatibility alias for the existing blocked-projection error type in the signing module, allowing the existing fail-closed mobile handler to import it, retaining the documented **zero-admitted-runtime** response on expiry rather than raising ImportError/500.

This is a separate narrow correctness fix in the same mobile-control acceptance path, not a new release promotion.

## Next production hold

- **Physical model server isolation:** Destroyer :1235, :1236 and Lenovo :1234, :1236 currently bind to wildcard addresses. The Fleet Model Link sidecar intentionally refuses those until a safe, rollback-ready change window. No existing service was restarted, rebound, or redirected.
- **Graph origin/custody proof:** Approve a real `openai_compatible` Fleet Model Link runtime, artifact hashes, and access/capacity observations *only from witnessed evidence* and preserving human authority. The synthetic fixture is never authority.
- **Local deployment admission:** pinned signer, signed catalog expiry, mTLS peer credentials, Tailscale ACL, gateway process identity, container-netns path, and isolated localhost backend. Do not assume HTTP localhost implies any safe client-to-node path.
- **Kipnerter:** never direct the iPhone to `http://127.0.0.1:19341` on a remote machine. Integrate via its already authenticated gateway and keep model handles opaque. Physical iPhone install/auth/render and streamer acceptance remain independent.
- **Session lineage:** a returned trace ID confirms only transport propagation. It is not an immutable receipt or a durable model-execution trace. The signed offline outbox and independent storage custody are separate requirements.

## October 11, 2026 engineering checkpoint

At PR head `7ab07f9e`, GitHub cross-repository-runtime-contract and implementer-handoff passed; general CI still had 75 failures (702 passed, 59 deselected), plus the independent source-binding recovery canary failure. Subsequent commits add default-deny independent operator proof with negative tests, and require **fresh exact-head CI**.

Neither a synthetic signed sidecar nor disposable signing key is physical-node approval. Destroyer/Lenovo socket bindings, ingress trusted-header stripping, approved graph evidence, and exact-build iPhone acceptance remain **unverified**. Keep PR draft and the feature disabled; do not modify active listeners.
