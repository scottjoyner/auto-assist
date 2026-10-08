# Provider presence on x1-370

This read-only monitor maintains a last-checked and last-reachable timestamp for
**one metadata endpoint per provider**. It never sends prompts, completion
requests, tool calls, or billable token-generation canaries.

## Providers checked
- OpenRouter public model catalog; this does **not** establish model availability.
- Kilo anonymous model catalog, with documented anonymous bearer.
- Direct Cohere catalog **only when** COHERE_API_KEY is passed securely in the
  monitor's environment; without one, shows credential_missing.
- Direct Grok/xAI catalog **only when** XAI_API_KEY is provided; availability
  does not imply free API token generation.
- OmniRoute local dashboard health (127.0.0.1:20128); expected port must be
  updated if deployed elsewhere.
- Auto-router local /health (127.0.0.1:8088).
- Local LM Studio model catalog (127.0.0.1:1234/v1/models).
- OpenCode CLI version probe. This is not a network inference provider.

Metadata probes never prove inference readiness. Keep completion canaries on
the independently rate-limited exact-model launcher, with trace manifests.

## Manual execution
`python3 scripts/provider_presence_watch.py`
Default private state:
`/home/scott/.local/state/auto-assist/provider-presence.json`.

The single-run executable is ready. Periodic service installation was not
confirmed during this pass; do not claim unattended freshness until an actual
timer and next tick have been verified.

## Limits and operational policy
Catalog probes every 30 minutes, local health/CLI every 15 minutes if
scheduled. An actual 429 or 503 backs off the affected provider for at least
one hour; 401/402/403 for 24 hours. No automatic retries. A file lock
prevents overlapping monitor instances that share a state file. Provider
latency is measured only for GET and is not token-generation speed.

Do not load credentials from checked-in files. Never persist bearer tokens,
headers, catalog response bodies, prompt text, or inference data in status.
The monitor records only endpoint URL, HTTP status, availability timestamps,
and bounded latency. Protect state with mode 0600 and its directory with 0700.

## Acceptance
1. No accidental paid completions (0 generation requests).
2. One catalog/health GET at most per configured interval and provider.
3. Isolated 429/503 and 401/403 cooldowns, no global retry storm.
4. Run a provider-attributed, approved minimal procedural canary separately
   before calling any provider *inference ready*.
5. If a single provider is not available, preserve its old success timestamp,
   surface the current error, and keep unrelated providers monitorable.
