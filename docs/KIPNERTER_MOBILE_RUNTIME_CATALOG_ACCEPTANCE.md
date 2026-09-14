# Kipnerter mobile runtime catalog acceptance

This slice makes AssistX the authoritative fleet/runtime discovery source for Kipnerter Agent Auto while keeping the phone observational.

## Mobile boundary

The route-scoped Tailscale Serve surface for Kipnerter now consists of exactly these AssistX paths:

- `/health`
- `/api/v1/auth/whoami`
- `/api/v1/runtime/catalog`
- `/api/v1/agent/chat/completions`

The AssistX host API remains loopback-only. Existing unrelated Serve roots and Funnel mappings are preserved.

## Catalog contract

`GET /api/v1/runtime/catalog` is authenticated by the same Tailnet identity boundary as `whoami` and agent chat. It is derived from AssistX's approved runtime projection and returns only:

- schema/source and projection freshness timestamps;
- fleet runtime/model counts;
- agent-capable and code-capable runtime counts;
- opaque runtime IDs, runtime kind, model count, and coarse capability flags.

It must not return node names, raw runtime IDs, model IDs, provider model names, artifact fingerprints, access URLs, ports, or executor/router credentials.

## Failure and fallback semantics

Catalog availability is observational. A catalog failure does not authorize the iPhone to infer fleet topology or weaken Agent Auto authentication. Direct LM Studio discovery remains a separate bounded fallback lane.

The gateway verifier requires the catalog Serve mount by default and, when catalog probing is enabled, requires HTTP 200, validates the sanitized schema/counts, and fails if forbidden routing/network detail appears in the response.

## Automated proof for this slice

The implementation wiring pass passed focused tests for mobile catalog sanitization, Tailnet-authenticated route behavior, sanitized backend failures, existing mobile agent routes, gateway wiring, Hermes router credentials, Python compilation, shell syntax, and `git diff --check` before committing the implementation.
