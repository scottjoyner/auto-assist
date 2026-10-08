# Observations 11 — synthetic trace-source HMAC and replay admission

**Date:** October 8, 2026 EDT. **Predicted beforehand:** `TRACE_ATTESTATION_PROSPECTUS_20261008.md`. **Execution:** x1-370, in a separate isolated research worktree. **Production code/service/keys NOT changed.**

## Baseline source review

AssistX already has `node_identity.verify_node_token()`, which verifies that a caller supplies a static secret registered for an agent/node ID. It does **not** bind a particular trace event to a source generation, task, correlation ID or digest. In the existing API, `/api/events` checks the event schema and operator authentication but treats the envelope's node/source labels as claims; node registration accepts submitted `SwarmNode` fields. The existing trace→Task→SwarmNode inspector in draft PR #128 correctly labels registry-ID matching as **unverified**. No externally trusted, replay-anchored trace execution attestation was located in this bounded source review.

## Implemented experiment

New **standalone** `src/assistx/trace_attestation.py`: a test-only stdlib HMAC-SHA256 verifier with versioned exact-field claim format, domain-separated canonical JSON, event hash, nonce, time window, node/agent/source service, registered key ID and generation binding. Exact-fields/duplicate-key rejection, 128-character bounded printable IDs, UUID normalization, hex digest/nonce format and constant-time HMAC comparison are enforced. The verifier consumes an independently supplied trusted `RegisteredSource` map and `ExpectedEvent` binding; it does not load keys from Neo4j, a trace payload or user input.

Optional `SandboxReplayLedger` stores **only synthetic** key ID, nonce, event ID, hash and acceptance timestamp in a caller-supplied SQLite file, with `BEGIN IMMEDIATE`, uniqueness constraints, FULL synchronous setting and WAL. Same key+nonce and same key+event ID replay attempts are rejected, including after ledger reopening and in concurrent threads. Mid-process ledger-file replacement/symlink swaps are detected from inode/device evidence and cause admission to stop.

No CLI, real keys, HTTP endpoint, production graph operation, NAS storage or agent action was added. Test fixtures use disposable fake keys and temporary directories. The verifier returns only a categorical safety decision—never a secret, source key or raw claim.

## Prediction/observation table

| Prediction or negative control | Observation |
| --- | --- |
| Correctly bound key/node/agent/generation/event digest | **Pass:** local cryptographic claim integrity plus SQLite test-only replay admission |
| Tamper event digest, correlation ID, task, source, node or agent | **Pass:** signature invalid, expected-event mismatch or registered-identity mismatch |
| Revoke/rotate/disable key or use wrong signing key | **Pass:** explicitly rejected |
| Expired, future, oversized validity window and invalid key validity dates | **Pass:** rejected; controlled test clock |
| Missing ledger | **Pass:** signature integrity can be reported, but replay acceptance **not** granted |
| Replay nonce or same event under another nonce | **Pass:** rejected after SQLite file reopened |
| Concurrent 4-thread replay | **Pass:** exactly one synthetic admission |
| Duplicate JSON keys, wrong types, malformed UUID, invalid hex, oversized text and envelopes | **Pass:** rejected before any replay update |
| Ledger swapped to symlink or replaced during verifier lifetime | **Pass:** fail closed |
| Existing node bearer token as substitute for event MAC | **Pass:** deliberately insufficient for event-specific claim integrity |
| Authenticated physical executor, independent producer, immutable off-host witness | **NOT PROVEN / not implemented** |

**Verification:** `PYTHONPATH=src python3 -m pytest -q tests/test_trace_attestation.py tests/test_trace_task_evidence.py tests/test_trace_context.py tests/test_trace_outcome_filter.py` — **102 passing Python tests**, of which **54 are new verifier tests** and 48 are previous trace evidence/context/outcome cases. `node --test tests/test_trace_investigation_ui.cjs` — **20 passing** existing UI tests. One unrelated Starlette TestClient/httpx deprecation warning persists. No external provider or live data. Static Python compilation and diff checks run separately.

## Semantics and acceptance: do not overstate

A local HMAC validates possession of a shared secret, **not a cryptographic signature that identifies uncompromised hardware or proves that the claimed machine executed work**. If the node and verifier both know a symmetric key, either could manufacture a MAC. A passing key check is *not* proof of correct producer-to-task assignment, lawful custody, completion or physical execution.

The SQLite file is **not** independently anchored, replicated, hardware protected or WORM. A rollback performed **before a fresh verifier starts** can evade its local inode continuity protection. This explicit research prototype lacks external key provisioning, rotation/incident response authority, secure hardware, witnessed source-generation enrollment, distributed durable nonce admission, trusted event digests, production API integration and source-to-NAS complete restore evidence.

The return contract makes this explicit: even successful local verification sets `claim_integrity_valid=true`, `local_replay_accepted=true`, but always `execution_attested=false`, `source_hardware_attested=false`, `custody_independently_witnessed=false`, and `production_authorized=false`.

## Release/no-go decision

**Sandbox research gate PASS; actual trace-to-node/agent identity verification NOT ACCEPTED.** Do not wire this to live HTTP routes or trust its claims in the UI until an independently controlled operator-provisioned identity registry, trusted event digest, producer generation, durable remote replay/witness store, and revocation lifecycle have been reviewed and demonstrated. Existing PR #128's unverified registry badge remains correct.

Next owner slice: identity source inventory and key custody prospectus; decide symmetric MAC vs asymmetric device-bound signing, independent enrollment and revocation; build signed/generation-bound synthetic agent→event→receipt envelope with external durable witness; demonstrate refused replay after server restart and rollback; then prototype a separate **read-only** verification status endpoint with explicit `not_established` defaults. Keep production fleet node tokens unchanged.

## References consulted

- NIST [FIPS 198-1, HMAC (2008)](https://doi.org/10.6028/NIST.FIPS.198-1); NIST 2025 notice indicates prospective transition to SP 800-224, not a license to represent the experiment as FIPS validated.
- NIST [SP 800-57 Part 1 Rev. 5, Recommendation for Key Management (2020)](https://doi.org/10.6028/NIST.SP.800-57pt1r5).
- NIST [SP 800-92, Guide to Computer Security Log Management (2006)](https://doi.org/10.6028/NIST.SP.800-92).
- [Auto-assist issue #125](https://github.com/scottjoyner/auto-assist/issues/125), [#123](https://github.com/scottjoyner/auto-assist/issues/123), and [#117](https://github.com/scottjoyner/auto-assist/issues/117).
