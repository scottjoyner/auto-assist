# Shared provider lease integration contract (draft, not deployed)

## Intended boundary
The prototype `scripts/shared_provider_leases.py` is an offline local
SQLite coordinator. It does NOT replace `auto_router.admission` or
open production routing. All existing locally enforced policies remain.

A future central service would live on x1-370 or a durable dedicated
container host, behind an explicitly reviewed authenticated endpoint.
Only verified free-provider upstream groups can receive policies.
An OmniRoute/OpenRouter/Space-Bunny aggregator must resolve a trusted
**physical upstream quota group** before admission. Do not assume that
vendor aliases represent independent free quotas.

## Proposed protocol (not a live API)
- `POST /v1/provider-leases/acquire`: provider_group, client_id,
  idempotency_key, reserved_input_tokens, reserved_output_tokens,
  ttl_seconds. Return allow/deny, opaque lease capability, expiry,
  reason code and correlation ID. Unknown provider => deny.
- `POST /v1/provider-leases/renew`: current lease proof and requested
  TTL. Fail closed on expiry, circuit open, wrong client or authority outage.
- `POST /v1/provider-leases/release`: idempotent release by same principal.
- `POST /v1/provider-leases/trip`: privileged reporting of actual
  upstream 429/503/401/402/403 and provider-specified Retry-After.
- `GET /v1/provider-leases/health`: redacted availability, no key,
  prompt, response, lease capability, or provider credential exposure.

Mutating methods require client authentication, authorized provider mapping,
bounded request sizes, replay-safe idempotency, strict canonical tenant and
provider IDs, authoritative host time, and per-provider rate limits. Do not
rely on Tailscale network presence alone as application authentication.

## Client-side acceptance requirements
1. A client MUST obtain the shared lease before any model dispatch and
   ALSO pass its existing local runtime admission and payment-free gates.
2. Client renews before TTL expiration; lost lease, lease expiry, server
   rejection, coordinator outage, or upstream circuit opening MUST stop
   request generation (graceful cancellation with bounded escalation).
3. HTTP 429/503 and auth/credit errors feed the shared provider circuit.
   No automatic paid model fallback and no retry storm.
4. Do not increase concurrency until every supported harness (Hermes,
   AutoClaw, OpenCode, OmniRoute, AssistX) either uses this lease or
   is explicitly excluded from the shared free provider's authority.
5. A remote coordinator must not use SQLite over CIFS/NFS. If using a
   single host SQLite control plane, clients call a mutually authenticated
   API on that host; otherwise choose a transactional service/store.
6. Store correlation IDs and monotonic event sequence with retention,
   but never log raw bearer credentials or prompts.
7. Verify 2+ real fleet machines contend for the same provider key,
   fault inject network outage, process crash, lease expiry, key rotation,
   provider 429, and clock skew before enabling live routing.

## Prototype status / reproducibility
Pre-registered hypotheses:
`docs/shared_provider_lease_prospectus_20261007.md`.
Offline executable:
`python3 scripts/experiment_shared_provider_leases.py`.
Observed synthetic evidence:
`artifacts/shared-provider-lease-pilot-20261007.json`.
Full suite: `python3 -m pytest -q tests/test_shared_provider_leases.py
tests/test_guarded_free_subagent.py tests/test_provider_presence_watch.py
tests/test_snapshot_free_usage.py tests/test_launch_kilo_exact.py
tests/test_summarize_free_trace_manifests.py`.
No provider API calls or inference processes were used. The audit chain
detects straightforward local edits but is NOT externally attested.

SQLite reference: https://www.sqlite.org/lang_transaction.html
Rate-limit reference: https://www.rfc-editor.org/rfc/rfc6585
