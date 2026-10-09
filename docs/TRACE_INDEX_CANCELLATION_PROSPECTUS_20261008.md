# Prospectus — Neo4j timeout and physical cancellation acceptance (trace index)

**Timestamp:** October 8, 2026 EDT. **Scope:** new branch research/assistx-trace-cancellation-20261008, stacked on unmerged admission/lease PR #154. **Decision:** no production activation.

## Gap being tested
The current draft trace-index capacity control uses a shared Redis token with a 30-second TTL and exact-token release. Once TTL expires, Redis can admit a successor **even if the old Neo4j read is still physically running**. The production Python list_traces count/page operations have *individual* 4-second driver query timeouts, but timeout is not in itself proof of physical cancellation during network stalls or driver failure.

## Predictions before measurement
1. A read-only intentionally expensive Cypher expression on isolated Neo4j 5.26 should trigger the Python driver's short transaction timeout (e.g. 1 second), returning an explicit error and allowing a subsequent read-only health query to finish. This tests cooperative server cancellation, **not** network-partition safety.
2. Follow-up read-only transaction inspection can observe zero remaining synthetic benchmark transactions under normal conditions, but absence of an exposed transaction does not guarantee every physical resource was released across all fault modes.
3. With a simulated Redis lease clock, a first query that lasts longer than its TTL permits a second admission while the first is still logically executing. This *negative control* must be retained and must not be called a fully fenced capacity lease.
4. The *current* route must continue to fail closed when occupancy capacity or rate budget is denied; tests must not silently authorize graph requests.

## Isolation and prerequisites
- Only newly created ephemeral Docker Neo4j 5.26 staging with its own **internal Docker network**, no host-facing ports or bind-mounted fleet data, one CPU, <=2200 MiB RAM, and disposable anonymous storage. All graph queries are read-only; no synthetic fixture data needed for intentionally expensive read calculations.
- The test script will reject wrong container name, wrong image/version, unexpected network, port publications, host mounts, auth configuration, memory/CPU caps, or unexpected inspected address. There is **no fallback** to production Bolt endpoints.
- Maximum three timeout probes (or fewer after a successful counterexample), explicit client/server connection/transaction deadlines and 2-minute hard wall stop per subprocess. No continuous monitoring/daemon, no background work.
- Record experiment environment, exact query intent, timeout classification, and read-only transaction inspection. Report only synthetic evidence, never private trace metadata/payloads or account secrets.

## Acceptance decision
A successful healthy-driver cancellation probe can only narrow risk under normal networking. Physical cap <=3 cannot be certified by a soft expiring Redis token under arbitrary network stalls or worker death without stronger query kill/renewal/fencing evidence. **Keep blocker #148 open and draft PR #154 unmerged** until a verifiable end-to-end deadline/fencing plan is reviewed and authenticated real API tests pass. Do not modify live Redis/Neo4j/AssistX services or archive/graph data.

References: PR #154, issues #148 and #123; Neo4j query timeout / transaction termination behavior must be interpreted cautiously.
## Controlled amendment before repeat probe

The first already-recorded synthetic calculation finished at all tested timeout thresholds and therefore **did not create a cancellation event**. The transaction-inspection query searched for its own numeric literal and inadvertently counted itself.

**Before repeating:** increase synthetic compute work to a 12,000-by-12,000 nested range with a dependent modulo condition and aggregation; retain the same three short driver timeouts and isolated container. Change transaction inspection to match only queries beginning with UNWIND, excluding the SHOW inspector. No graph writes or real traces. Record both runs separately; the first is an inconclusive load case, not a successful deadline test.
