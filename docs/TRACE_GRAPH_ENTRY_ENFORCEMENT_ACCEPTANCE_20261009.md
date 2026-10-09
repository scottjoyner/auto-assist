# AssistX #148 — graph entry enforcement acceptance contract

**2026-10-09 — design-only, research/draft, NO PRODUCTION AUTHORIZATION**

Parent: draft #249 (PostgreSQL admission and independently witnessed Neo4j closure). This follow-up specifies the smallest *enforced* entry gate. It does not claim implementation or physical validation.

## Threat model / invariant

A worker may be buggy or malicious: it can omit query comments, forge markers, replay permits, open extra Bolt connections, retry after timeout, or continue after a worker crash. The safety invariant is:

> Every physical Neo4j transaction initiated by a workload must be attributable to a live, exclusive, authoritative PostgreSQL admission, and unknown physical state must retain capacity.

A marker in worker-authored Cypher is **not** authorization. A gateway accepting a self-reported reservation is **not** authorization. Counting HTTP requests is not counting physical graph transactions.

## Proposed smallest enforceable slice

1. **Only gateway may reach Bolt:** isolated container network and firewall deny worker-to-Neo4j routing; deny workers Neo4j credentials; graph read role is gateway-only with minimal privileges. Include connectivity negatives from an actual worker container. Research fixture only.
2. **Server-controlled binding:** trusted gateway obtains a reservation via restricted PG admission, assigns a gateway-generated immutable operation ID and attempt ID, and starts a single graph transaction. Bind PG reservation/term to graph transaction by server-observed transaction metadata and gateway-controlled connection identity, not a worker-supplied Cypher marker. If server-side observation is ambiguous, deny/restrict release.
3. **Custody:** observer/verifier is separate from worker and gateway credentials. Worker or gateway cannot invoke SQL release. Release only after observer has bound exact instance generation, database, connection/transaction identity and absence evidence; missing telemetry, partition, restart or ambiguous identity => QUARANTINE and no capacity reuse.
4. **Retry and timeout:** a retry, reconnect or gateway crash does not start a second physical attempt unless its own reservation is admitted. A timeout or interrupted client request is not proof of termination. Reject stale term, token and duplicate receipt.
5. **Gateway fail-closed:** PostgreSQL outage, unverifiable term, Neo4j observability outage, audit write failure, or unknown physical transaction state prevents *new* graph entry. Preserve existing capacity until independently reconciled.
6. **Scoped exposure:** no API routes, production network changes, migration, or live user data. Use separate disposable PG17 / Neo4j 5.26, internal-only no published ports/mounts, opt-in safety guard, ephemeral keys, and redacted receipts.

## Explicit adversarial acceptance matrix

| Case | Required result |
|---|---|
| Direct worker Bolt query, no admission | Network/auth REJECT; Neo4j shows no workload tx |
| Worker supplies forged marker or reservation ID | Gateway ignores worker authority; no graph tx without PG grant |
| Worker sends two concurrent queries at cap=1 | One physical tx admitted; second denied |
| Gateway commits PG reservation but fails before Bolt connect | Slot held until reconciler proves no graph attempt; no optimistic release |
| Bolt query active after worker SIGKILL, relay blackholed | Slot retained, successor denied |
| Gateway crash or loss of PG connection with Neo4j still running | Quarantine; successor denied |
| Uncertain tx identity, two plausible matches, lost observer | No release |
| Wrong token, stale term, replayed closure receipt | No release |
| Audit writer unavailable | No release; no unsigned closure |
| Instance restarts / generation changes before absence proof | No release without independent reconciliation |
| Separate copy of single-primary PG authority | Both may admit; record as **known negative** until quorum term is implemented |
| Reverse-proxy identity, NAT fairness, 1/3/5/10 API clients | **Not covered by this slice**; remain #148/#149 gates |

## Minimum evidence schema (append-only witness intent)

`event_id`, `event_type`, `event_time_utc`, `operation_id`, `attempt_id`, `reservation_id`, `admission_epoch`, `fencing_term`, `pg_primary_identity`, `neo4j_database`, `neo4j_server_identity`, `neo4j_generation`, `neo4j_transaction_id`, `observed_state`, `observer_identity`, `previous_event_hash`, `event_hash`, `signature_key_id`, `signature`.

Event types: `ADMISSION_COMMITTED`, `GRAPH_ENTRY_BOUND`, `ACTIVE_WITNESSED`, `CLOSURE_UNCERTAIN`, `ABSENCE_WITNESSED`, `RELEASE_AUTHORIZED`, `RELEASE_REJECTED`, `QUARANTINED`.

Use canonical serialized fields, durable append-only storage and independent key custody. Hash chaining by itself is *not* tamper-proof against a privileged rewriter; independent checkpointing and revocation policy remain separate requirements. Do not place raw Cypher, credentials, identifiers containing PII, or query payloads in receipts. PostgreSQL verifier-role possession alone is still a release authority pending DB-enforced receipt checks.

## Acceptance decision

**Entry gate GO (research only)** requires complete adversarial results from physical disposable Neo4j+PG, demonstrably unreachable worker Bolt interface, observed server transaction identity not derived solely from worker markers, verified custody and fail-closed cleanup. No production gate is cleared by synthetic unit tests alone.

**Distributed failover remains NO-GO** until monotonic independently controlled term ownership, quorum fencing, stale effects rejection, and uncertain-in-flight reconciliation all pass on real isolated hosts. Two absent SHOW TRANSACTIONS samples from one reachable server do not establish cluster-wide closure.

The parent PR #249 remains draft; issue #148 stays open.