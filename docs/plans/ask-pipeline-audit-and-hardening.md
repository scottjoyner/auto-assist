# Ask Pipeline Audit and Hardening Plan

## Immediate fix
- Normalize ask questions before logging or dispatch.
- Keep the ask request model explicit about non-empty question text.
- Add regression coverage for whitespace-heavy and punctuation-heavy asks.

## Audit scope
- Review all UI entry points that submit user text to backend APIs.
- Review all FastAPI models for implicit coercions and default extras behavior.
- Review all Neo4j write paths that accept user-generated text.
- Review all LLM prompt builders for raw string interpolation.
- Review all background job entry points for unhandled exceptions and raw error leakage.

## Next update batch
- Standardize request/response schemas across ask, task, and dispatch APIs.
- Add structured error envelopes for user-facing failures.
- Expand API regression tests for sync, async, and auto ask modes.
- Tighten docs so the UI contract matches the actual API behavior.
