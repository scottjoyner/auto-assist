# OpenCode free-model registry: safe read-only qualification checkpoint

Date: October 9, 2026, approximately 17:55 EDT.
Context: knowledge issues #55 and #57; auto-assist mock lease PR #229.
Status: RESEARCH / NO DISPATCH / NO PROVIDER GENERATION CALLS.

## Prediction

A live connected OpenCode provider ID and reported zero input/output pricing
establish discovery metadata ONLY. They are not evidence that a provider
has a zero-dollar billable account allowance, independently attributable
upstream quota, or safe multi-agent admission.

## Implementation

scripts/read_only_free_catalog_readiness.py accepts a supplied OpenCode
/provider JSON response via file or stdin. It does not make network requests,
look up API keys, inspect environment credentials, write records, or call a
model. All outputs are allowlisted and projected from the input. Unknown
provider names, model IDs and secret-looking fields are never passed through.

It checks five explicit provider IDs: zai, cohere, opencode, openrouter, and
kilo_free. The model allowlist has 11 exact candidate IDs. Every output has
dispatch_authorized=false, qualification=unqualified, no model usage receipt,
no independently qualified upstream quota and zero provider calls.

Safety rules: strictly connected ID; model status active; finite numeric
exact-zero input AND output catalog prices; no automatic fallback;
ambiguous duplicate provider IDs denied. Missing prices, NaN/Inf, aliases,
wrong IDs, untrusted eligibility claims and malformed schema fail closed.
Even passing all catalog gates only produces catalog_only_unqualified.

## Actual observations from x1-370

At read-only inspection on October 9, 2026, the authenticated local OpenCode
provider metadata at 127.0.0.1:41779/provider listed all 11 candidates
as connected, active, and with advertised zero input/output catalog price.

- zai: glm-4.5-flash; glm-4.6v-flash; glm-4.7-flash (3 catalog candidates)
- cohere: north-mini-code-1-0 (1)
- opencode: ling-3.1-flash-free; mimo-v2.6-flash-free;
  nemotron-3-ultra-free (3)
- openrouter: cohere/north-mini-code:free;
  thinkingmachines/inkling-small:free (2)
- kilo_free: dots-studio/dots-3-note-preview:free;
  nvidia/nemotron-3-ultra-550b-a55b:free (2)

Result: 11/11 catalog candidates, 0 provider generations, 0 genuinely
qualified independent upstream groups, 0 routes authorized to dispatch.
Direct provider names do not prove independent billing accounts.
OpenRouter/Kilo model aliases can share an upstream provider's quota;
the tool never asserts they are separate pools.

No credential value, endpoint token, prompt or full registry JSON was
included in this evidence document.

## Test evidence

Focused offline no-network unit suite:
- x1-370: 36/36 PASS, py_compile PASS.
- xwing: 36/36 PASS, py_compile PASS, disposable isolated /tmp files
  cleaned by the test shell after completion.
- Actual x1-370 read-only registry projection returned 11/11 catalog-only
  candidates with dispatch_authorized=false and zero model calls.

## Required next gate — not authorized by this document

1. Independently identify billing account scope, physical upstream
   quota group and permissible zero-cost conditions per exact model.
2. Acquire authorization for bounded authenticated test generation
   and verify the returned exact model, cost, token usage and quota movement.
3. Verify shared alias grouping, 401/402/403/429/503 state, available
   quota and real provider dispatch cancellation with a single fenced issuer.
4. Establish off-host trace/audit witness and reconcile uncertain in-flight
   effects. Preserve zero paid fallback and three-session global limit.
5. Independent review, current-main reconciliation and applicable CI must
   complete before broad fleet routing. Knowledge provider checkpoint
   CHECKPOINT_NOT_REACHED remains binding.

This isolated catalog-only tool neither advances the live quota checkpoint
nor grants new provider/model execution permissions.
