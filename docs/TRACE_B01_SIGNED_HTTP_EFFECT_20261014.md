# B01 signed-only HTTP mutation gateway — disposable proof, 2026-10-14

**Classification:** Synthetic-only research. **Production: NO-GO / HOLD**.
Parent: [draft Auto-Assist PR #356](https://github.com/scottjoyner/auto-assist/pull/356)
`1126911893d04845c3c960f3e064e3e73104b5b2`.
Related Context research: [Kipnerter API issue #46](https://github.com/scottjoyner/kipnerter-api/issues/46).
Nothing here is installed on the production API, worker, Neo4j, router, or
any existing service. No live database, credential, port, network, TPM, NAS,
phone, deployment, admission, or failover changes were performed.

## Why this experiment is distinct from PR #356

PR #356 proved two Ed25519 signatures in an *external local verifier* and
showed that a stolen Neo4j writer credential bypasses that verifier. The
Context native PostgreSQL 17 lab separately demonstrated actual in-database
Ed25519 rejection of forged evidence under a compromised ordinary authority
SCRAM credential; an actual `pg_restore` then reaccepted a previously valid
signed term because its database-local high-water had been rewound.

This B01 slice answers a narrower transition question: can a fixed-plan,
signed-only **HTTP mutation gateway** that exclusively owns the graph writer
credential protect an unprivileged worker with a stolen gateway bearer against
forged admission, while correctly preserving PENDING uncertainty and physical
network isolation? Does the writer bypass remain reproducible?

## Exact synthetic authority and data paths

`trace_b01_signed_claim_http_gateway.py` binds only
`POST /v1/fixture-claim` and read-only `/health` inside a disposable Python
container. Requests require an ephemeral bearer and exactly one signed proof
envelope with two detached Ed25519 signatures. Pinned fixture roots and a
pre-existing SQLite plus fsynced checkpoint are **server-owned**, never
selected by a caller or dynamically enrolled from headers. Duplicate JSON
keys, caller-specified Cypher, arbitrary digests, resource URI, signer roots,
and extra fields are rejected. The gateway verifies and reserves the signed
claim **before** executing a single fixed Neo4j `FixtureFence/FixtureTask`
transaction. Caller errors do not disclose signatures, internal queries,
credentials or payloads. Results retain `PENDING_UNCERTAIN` and explicitly
refuse production authority, automatic retries, and release authorization.

A worker-only disposable Python actor has no Neo4j auth package or writer
credential; it shares only the worker network with the gateway. The gateway
alone also joins an isolated Neo4j graph network. Graph Bolt/HTTP are not
host-published. Fixture graph labels are **not** the real product `Task`.

Ephemeral Ed25519 private keys and all graph passwords are generated in the
same test orchestration process on x1-370. This is NOT independent device
signer custody, a hardened cryptographic root, mTLS, or production identity.
The writer gateway's container itself holds a valid graph writer credential,
and a privileged compromise still bypasses all its Python code.

## Physical acceptance — x1-370, opt-in disposable only

`ASSISTX_B01_SIGNED_HTTP_PHYSICAL=1 python3 tests/probe_trace_b01_signed_claim_http_physical.py`
creates fresh, uniquely labelled private Docker networks, a Neo4j 5.26
Enterprise graph, a dual-network gateway, and a worker-network actor. Only
cached local images and offline-copied cached Python modules are used; no
pip/network download or graph mount. The disposable Neo4j role receives only
narrow fixture node MATCH/SET grants. The test uses no production secrets.

**Physical result: PASS_SYNTHETIC_WITH_WRITER_BYPASS_NO_GO**, exit 0.
The repeat test with a disposable gateway restart finished successfully in
approximately 38 seconds. It established:

- Real worker HTTP request with valid dual signatures and a random bearer
  performed one fixed, synthetic `FixtureTask READY -> CLAIMED` mutation.
- Forged issuer and device Ed25519 signatures, tampered agent identity,
  missing bearer, arbitrary Cypher and digest-only requests all denied.
- Worker could not TCP-connect to graph Bolt or HTTP by raw graph IP or
  Docker hostname but could connect to gateway; no host graph ports.
- Immediate replay denied. A disposable gateway **process restart** reopened
  a pending SQLite ledger record with high-water 1 (no auto-clear/reissue).
- Physical graph-only rewind back to READY/high-water 0 did not enable replay
  of the prior signed envelope through that restarted gateway.
- **Intentional NO-GO:** the same restricted writer credential issuing
  unreviewed Cypher directly changed the Task status *without* advancing
  graph high-water or consulting the signed gateway. This remains a real
  authorization bypass under a privileged writer compromise.
- The fixture containers and private networks were removed in the runner's
  `finally`; exact-head acceptance and cleanup are separate checklist items.

Unit regression independently checks strict envelope shape and duplicate
JSON keys, denied unauthorized bearer, and crash/error after reservation:
failed graph calls result in HTTP 503 `UNCERTAIN_GRAPH_EFFECT` without an
automatic second write or releasing the pending reservation.

## Still not proven — release requires independent boundaries

1. A non-bypassable graph **write resource** that enforces pinned Ed25519
   roots, a single recognized principal and fixed authorized operation
   **inside the unavoidable effect**. Restricting the client network is
   necessary but the compromised gateway/Neo4j writer remains powerful.
2. Externally protected monotonic high-water and trust root outside the
   graph **and gateway's SQLite/anchor co-restore domain**. A gateway
   restart is not remote rollback resistance, and source-based key pinning
   without protected re-enrollment is not durable key custody.
3. Real signed source + device identity and recovery policy provenance,
   revocation, time validation and independently checked post-COMMIT witness.
   Do not equate two locally generated ephemeral keys with fleet witnesses.
4. Atomic or fail-closed uncertain-inflight coordination between a remote
   replay register and graph effects. A crash after reservation or graph
   commit must not enable duplicate work, stale terms, or failover.
5. Full live graph consumer inventory and real-image parity for claims,
   heartbeat, complete, allocation and alternate direct-Cypher paths;
   production B01 still has **seven** non-graph Docker containers with
   observed direct Neo4j Bolt TCP reachability, plus published host ports.
6. Exact GitHub-head tests/CI, independent security review and an explicit
   separately approved rollback/cutover plan before any production mutation.

**B01/B02/B06 remain HOLD. Keep this PR draft/unmerged.**