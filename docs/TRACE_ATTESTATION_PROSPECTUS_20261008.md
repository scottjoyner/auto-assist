# Prospectus 11 — independently bound trace identity claim verifier (2026-10-08 EDT)

**Recorded before prototype code or tests.** Stacked on review-only [PR #128](https://github.com/scottjoyner/auto-assist/pull/128). **No production endpoint, credentials, Neo4j mutation, NAS write, token rotation, or agent execution.** Use only synthetic keys/claims in temporary test directories.

## Source analysis and threat model
- `verify_node_token()` currently checks a static registered node token with `hmac.compare_digest` on certain API routes; a token check on a separate request does not bind a specific trace event, source generation, task or worker to the authenticated node.
- `/api/events` validates the canonical event envelope and uses operator authentication, but accepts source/node/actor identity as event fields rather than independently proving physical executor identity.
- `/api/swarm/nodes/register` updates a `SwarmNode` from a submitted registration payload. Existing registry entries/Task.node_id values are **observations**, not independently signed proof.
- PR #128 surfaces exact task→node registry equality but accurately labels it unverified. That gate cannot be closed with string matching.
- Static bearer secrets can be stolen; HMAC proves possession of the provisioned secret, not uncompromised hardware, that execution happened, or that historical log custody was preserved.

## Falsifiable predictions
1. A strict schema-bound, canonical HMAC-SHA256 **research verification** can bind: exact `key_id`, registered `node_id`, `agent_id`, source service, `generation`, UUID correlation ID, task ID, event SHA-256 digest, issued/expiry timestamps and a high-entropy nonce. Swapping any bound field invalidates the signature or policy.
2. Unregistered, disabled/revoked, rotated-generation, wrong-service, wrong-node, wrong-agent and stale claims must fail, without a fallback to unsigned registry matching.
3. A **durable, independently supplied replay store** (sandbox SQLite on a test-only path, WAL/transaction and unique nonce per signing key) rejects nonce replays across store reopen. The verifier without the store may report signature integrity but must never report complete replay-safe admission or production-ready attestation.
4. Strict schema rejects duplicate JSON keys, missing/extra keys, invalid UUIDs, control characters, nonintegers, malformed hashes/signatures, clock skew and claims valid for too long. Use time injected by tests, never inferred fleet clock.
5. All positive results must say `claim_integrity_valid=true` but `execution_attested=false` and `production_authorized=false`. HMAC+replay does **not** attest machine hardware, executor integrity, cryptographic custody, or signed event provenance.
6. No signing keys or raw inputs enter UI, graph, GitHub, logs, reports or model endpoints. Fixtures use disposable fake credentials.

## Measurement and acceptance
Build stdlib-only verifier with immutable registered identities, canonical JSON, constant-time signature check and SQLite test replay ledger. Add at least 20 adversarial tests including cross-node/agent, revoked/rotated, duplicate nonce across ledger restarts, concurrent admission, clock tolerance, collision cases and tampered hash. If any authority boundary is ambiguous, fail closed. Add documentation and draft LaTeX with predictions/results and citations to NIST HMAC (FIPS 198-1), NIST key management (SP 800-57), and OWASP replay considerations, distinguishing proposed design from deployed fleet attestation.

**Release gate remains open:** no trusted production key provisioning, hardware attestation, signed trace ingestion, independent WORM custody, authenticated browser acceptance, or empirical fleet source identity has been established.
