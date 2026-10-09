# AssistX #148 — PostgreSQL admission ↔ independent Neo4j release witness

**October 9, 2026 | RESEARCH ONLY | #148 and #149 remain OPEN / NO-GO**

## Hypothesis and prerequisites

Can an admitted graph worker with *only a PostgreSQL worker credential* start a real Neo4j read; can an independent observer with the *only verifier-role credential* refuse to release a nonexpiring Postgres slot across worker SIGKILL and a blackholed Bolt socket; and will the slot be released only after Neo4j itself shows the previously observed server transaction absent on two independent snapshots?

This proof extends [draft #214](https://github.com/scottjoyner/auto-assist/pull/214) (SQL `SECURITY DEFINER` cap and SCRAM privileges) and [draft #209](https://github.com/scottjoyner/auto-assist/pull/209) (real Neo4j 5.26 blackhole / server-side closure). The integrated code is **NOT API-route-wired**. No production query admission, proxy header or provider behavior was changed.

## Physical fixture and custody

Both exact named images were already cached locally and ran simultaneously on x1-370:

- `assistx-trace-privilege-pg-20261009`: cached PostgreSQL 17 Alpine, own isolated **internal-only** Docker bridge `assistx-trace-privilege-net-20261009`, host ports **none**, bind/persistent host mounts **none**, 1 CPU/512 MiB, temporary PGDATA and SCRAM-SHA-256.
- `assistx-physical-trace-probe-20261009`: cached Neo4j 5.26 Enterprise on a **separate internal-only** Docker bridge `assistx-physical-trace-net-20261009`, host ports **none**, persistent host mounts **none**, 1 CPU/1800 MiB, small tmpfs data/logs, `NEO4J_AUTH=none` *only inside this isolated fixture*. The default `neo4j` database needed a disposable-instance-only read-only setting removed to complete ephemeral startup; no production Neo4j config touched.
- Docker target checks in `probe_trace_pg_privileged_roles._address` and `probe_trace_physical_lifecycle_526.checked_uri` require exact instance name/image/network/IP and absence of ports/bind mounts. Both require explicit opt-in flags. The signer private key is generated **inside the separate observer process**, not given to the worker or printed.
- The operator/test-harness bootstrapped SCRAM fixture roles and PostgreSQL metadata with a fresh UUID4 epoch. Ephemeral passwords were generated in local `0600` files not added to Git or displayed. The test-harness *is not a production independent trust authority* because its bootstrap scope can see all fixture credentials.
- For this experiment, the administrator set **capacity 1**, independently from the worker SQL role. The worker can invoke only the server-enforced `admit`/`inspect` functions. The observer alone can invoke the verifier-only `release_exact`. Production independent server-secret custody remains unproven.

## Actual observed results, two reruns

```text
admission: 1 worker → PostgreSQL worker-only SCRAM role → server-enforced reservation COMMIT
graph read: worker starts long-running Neo4j UNWIND transaction with research token marker
observer: separate process confirms exactly one active server transaction ID
wrong token: refused
early release: refused while server transaction active
transport: disposable localhost TCP relay silently drops bidirectional traffic,
           retaining its upstream Neo4j Bolt connection
worker: SIGKILL (-9) while Neo4j continues server-side work
successor: denied (PG cap 1 remains occupied)
observer: refuses again while old Neo4j transaction visible
transport closure: relay socket deliberately closes
observer: two independent SHOW TRANSACTIONS snapshots show old tx absent
verifier: separate PostgreSQL SCRAM verifier-role release_exact succeeds once
signature: observer emits Ed25519 receipt; outside observer, signature verified
tampered signature: denied by independent public-key verification
replay: refused
successor capacity: PG inspect returns 0
```

First physical run: server transaction `neo4j-transaction-1`. Second run after test-only PG restart: `neo4j-transaction-2`. Second run additionally verified the Ed25519 signature outside the observer and rejected a tampered token/receipt. Both passed. The fixture ran no production graph contents or Postgres data.

The Python test driver is `tests/probe_trace_pg_neo4j_witness_custody.py`. Offline synthetic guards in `tests/test_trace_pg_neo4j_witness_guards.py` refuse missing opt-in, wrong token, ambiguous/multiple candidate Neo4j transaction IDs, wrong database and absent matching query. The dedicated GitHub Actions workflow runs **only synthetic offline guards** and never starts Docker or touches Neo4j/PG.

## Security boundaries and mandatory release holds

1. **Worker-controlled query comments are not server-owned authorization.** The observation associates server transaction ID with a syntactic query marker, not an unforgeable Neo4j server-issued permit. A rogue worker may skip admission and issue an unmarked read, or stamp identical markers on other queries. A production solution must prevent any query from reaching the graph without a *server-enforced* admission decision; query comments alone do not confer that.
2. **The witness can still self-release via its verifier credential.** The PostgreSQL SQL function does not verify an Ed25519 receipt or independently interrogate Neo4j; custody depends on the observer program and its separate credential. The emitted signature was verified independently in the *research harness* but not stored as an append-only authoritative audit record. Production custody, signing-key lifecycle/escrow, replay ledger, and revocation need dedicated acceptance.
3. **Absent twice is not cluster consensus.** Two successful SHOW TRANSACTIONS reads only establish absence on the reachable test instance at two times. They do not prove an unreachable Neo4j partition is stopped, transaction IDs cannot be recycled after restart, or failover is safe. A production verifier must pin server generation/identity, hold uncertain-in-flight capacity, and refuse releases through partitions/cluster membership changes until independent reconciliation.
4. **PostgreSQL is still a single writer.** The SQL cap is strong when the one primary remains authoritative and reachable, but two copied/rolled-back primaries or independent restored epochs can overadmit. Requires independent monotonic fencing terms and quorum-backed primary ownership; safe failover/recovery must preserve and reconcile in-flight slots.
5. **Only one real graph query in this new integrated probe.** Earlier separate role-fence/PG and Neo4j tests exercised 1/3/5/10 workloads and transport failures, but this combined PG+Neo4j witness test is one capacity-1 query. Multiworker multi-host end-to-end performance, identity/role privacy, real API ingress #149, error budgets and p95/p99 not established.
6. **No production wiring or deployment.** Flags, services, Neo4j/Postgres credentials, databases, queues, Redis, Tailscale, NAS and API paths were unchanged. Do not use draft CI or research receipts as release authorization.

**Next smallest gate:** run multiple actual admitted Neo4j transactions with server-enforced graph entry, independently signed append-only closure audit, and controlled single-primary loss while a blackholed Neo4j transaction remains active. Then prototype quorum/monotonic-term fencing, without automatic takeover until independently witnessed in-flight reconciliation.
