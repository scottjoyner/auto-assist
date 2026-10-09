# AssistX #148 — guarded graph-entry physical and lifecycle handoff

**2026-10-09 | Research-only, draft PR #251 stacked on #250 → #249 | PRODUCTION NO-GO**

## Decision and hypothesis

Hypothesis: a narrow trusted entry path can require PostgreSQL admission before starting a real Neo4j read, reject arbitrary worker Cypher, retain reservations across uncertainty, and leave observable evidence that doesn't leak tokens. This is a bounded *research* step toward #148, not a distributed failover or #149 API-entry release.

### Concrete implementation

- `tests/trace_graph_entry_guarded.py`: immutable registry of source-reviewed query plans, strictly bounded/serialized parameters, gateway-owned operation/attempt IDs, epoch-matched PG grant checks, append intent before graph, hold capacity on exceptions/cancellation. **No release method.**
- `tests/trace_graph_entry_research_journal.py`: synchronous SQLite local commits, ordered lifecycle transitions, SHA-256 chained events, reopen/verify, no tokens/Cypher/parameter bodies. Application-level append only; not independently immutable.
- `tests/trace_graph_entry_metadata.py`: strict Neo4j `SHOW TRANSACTIONS.metaData` matcher. Rejects no match, ambiguity, malformed IDs, wrong epoch/attempt, wrong DB and type-confused term. Gateway supplies metadata; Neo4j merely reports it.
- `tests/probe_trace_entry_network_isolation.py`: opt-in disposable Neo4j 5.26 Enterprise, two *internal* Docker bridges, worker container direct Bolt IP denial and gateway real Cypher success; separately observes driver-initiated metadata on Neo4j.
- `tests/probe_trace_entry_pg_neo_smoke.py`: opt-in disposable PG17 SCRAM + Neo4j 5.26, restricted `SECURITY DEFINER` admission, guarded gateway read, cap-1 successor refusal, worker release privilege denial, local journal retention. No production routes touched.
- `.github/workflows/trace-graph-entry-research.yml`: focused offline tests only, physical probes require explicit opt-in and are never launched by CI.

### Physical evidence — x1-370, October 9

**Isolation / transaction metadata probe: PASS (second version, ~19.30 seconds)**

- Actual Neo4j 5.26 Enterprise in a new internal Docker network, no published ports or bind mounts.
- Separate internal worker network: direct Neo4j IP/Bolt request refused.
- Separate authorized gateway-network client: TCP connection and actual `RETURN 1` succeed.
- Independent query of `SHOW TRANSACTIONS` while gateway-managed transaction is open: exact gateway-created `metaData` was observed and wrong-attempt match rejected.
- **Critical limitation:** the host test harness itself can reach internal Neo4j IP. Only the specifically isolated worker container was denied. This is not proof that every host process, Tailscale user, or agent cannot bypass the gateway. Neo4j auth was disabled solely within this disposable fixture.

**Combined restricted PostgreSQL + Neo4j smoke: PASS (corrected rerun, ~17.54 seconds)**

- Real PostgreSQL 17 Alpine SCRAM and Neo4j 5.26 Enterprise; fresh isolated network, no published ports or host binds.
- PostgreSQL worker-only role successfully called restricted admission, then guarded gateway executed `RETURN $value AS value` and obtained expected `42`.
- Unregistered query plan refused before graph; a second request at cap=1 denied despite first driver's successful return, retaining the reservation.
- Worker role could not invoke PostgreSQL `release_exact`; journal reopened/validated 3 events.
- **Critical limitation:** `Grant.term=1` was explicitly a *fixture placeholder*. The current research PG schema carries an epoch but does not issue an independently monotonic fencing term. This cannot be represented as quorum fencing.
- Independent witness closure from #249 was not integrated in this run; reservation intentionally remained held until disposable PG destruction. No trusted cleanup/release capability was exercised.

**Cleanup:** After the runs, x1-370 checks found no remaining `assistx-entry-*` containers or networks. Production containers on x1-370 (including its existing Neo4j and AssistX services) were not changed.

### Test evidence and constraints

- Parent #250: 13/13 offline graph-entry tests PASS.
- First #251 focused lifecycle suite: 36/36 PASS.
- Added server metadata negative cases: 40/40 focused PASS on x1-370 before final malformed-plan addition.
- Added an unhashable plan-ID negative test and an offline-only GitHub workflow. Re-run new exact-head suite and hosted CI before citing a new number.
- Full repository local collection on isolated worktree did **not** complete: `ModuleNotFoundError: langgraph`. This is an environment dependency blocker, not proof of full-suite success or of a source regression.
- GitHub connector owns remote commits; x1-370 Git HTTPS push lacked noninteractive credentials, so local hardening worktree and remote head are not identical. GitHub branch is the proposed PR authority; use exact-head verification.

### Remaining safety failures and decision gates

1. **Gateway admission still not unbypassable on the host.** The physical worker-network probe is only one Docker network boundary. Production requires network policies/host firewall enforcement plus per-gateway Neo4j read-only credentials and removal of direct worker credentials/routes; test from each fleet node, Docker host network, proxy, and alternate Bolt interfaces.
2. **Metadata isn't an unforgeable permit.** A compromised gateway could emit duplicate/misleading metadata. Use independently verified PG grant with DB-enforced fencing, exact server-instance generation and independent observer binding; ambiguity quarantines capacity.
3. **Auditing is local, mutable and unanchored.** SQLite SHA chains can be rewritten or truncated by a privileged actor. Real custody needs an independent signer, append-only externally anchored receipts, replay/revocation ledger, durable persistence and audit-failure injection before any SQL release.
4. **Single-primary recovery remains unsafe.** No independently governed fencing term, quorum ownership, copied-state negative recovery or in-flight reconciliation is implemented. Quorum-backed monotonic terms and stale-effect rejection remain required.
5. **Real API and performance gates remain open.** The combined probe is a single authorized query; no authenticated 1/3/5/10 trace-index API clients, p95/p99, proxy identity/NAT fairness, mobile/browser or rollback acceptance.
6. **Driver success isn't closure custody.** The gateway never releases a reservation. Integrate #249's independent observer only after the exact metadata/instance binding and audit custody are independently accepted. Never release on TTL, client exception, worker SIGKILL or a single absent snapshot.

### Smallest next acceptance

A true three-party fixture: isolated worker with *no Neo4j connection path*, gateway with exclusive read-only Bolt credential and PG worker-only grant, and independent witness holding verifier-only PG credential plus separate signing key. Run cap=1 real Neo4j transactions across blackhole, worker death, gateway death, PG outage, duplicate metadata, audit-write failure and instance restart; require successor refusal until witnessed closure. Then introduce externally governed quorum fencing terms and replay-safe fail-closed takeover (research only). Do not deploy to production or close #148 until each negative gate is reproducibly green.

**Related:** [#148](https://github.com/scottjoyner/auto-assist/issues/148), [#149](https://github.com/scottjoyner/auto-assist/issues/149), [draft #249](https://github.com/scottjoyner/auto-assist/pull/249), [draft #250](https://github.com/scottjoyner/auto-assist/pull/250), [draft #251](https://github.com/scottjoyner/auto-assist/pull/251).
