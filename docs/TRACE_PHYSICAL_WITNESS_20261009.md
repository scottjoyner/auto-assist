# AssistX #148: Real Neo4j physical-work observation and Redis-loss quarantine

**2026-10-09 — RESEARCH ONLY, NO PRODUCTION AUTHORITY.** This branch stacks on draft #186 (durable single-host SQLite ledger). No trace API routes import it and no runtime, Redis fleet, production Neo4j, NAS, proxy, provider, credentials or auth configuration was changed. All actual work was against deliberately created, standalone Docker containers with **internal-only networking, no published ports, no host bind mounts, labels, CPU/memory/pid caps**.

## Preregistered target controls

- Neo4j: exact `neo4j:5.26.26-enterprise` image, container `assistx-trace-physical-20261009`, `--cpus=1 --memory=2g --memory-swap=2g --pids-limit=220`, `NEO4J_AUTH=none` **only on disposable Docker internal network** `assistx-trace-physical-net-20261009`; no volumes or host ports. Validation refuses wrong name/image/label/external network/port binds/host mounts/oversized caps before opening Bolt.
- Redis: exact `redis:7-alpine`, container `assistx-trace-redis-physical-20261009`, `--cpus=0.25 --memory=128m --memory-swap=128m`, no persistence (`--save '' --appendonly no`), no host binds/ports, same disposable internal network. Never connects to fleet Redis.
- The worker ran a bounded synthetic, read-only calculated Cypher query (`UNWIND` integer ranges and a modulo predicate), marked with a unique random synthetic ID. An **independent driver** polled `SHOW TRANSACTIONS` with a parameterized marker and recorded the Neo4j-owned transaction ID. No production trace or source corpus was accessed.
- Single-host SQLite `trace-ledger-test.sqlite` was created only under a disposable `/tmp/assistx-trace-ledger-test-*` directory, with a pinned epoch and one nonexpiring occupied token. Ledger read path uses SQLite `mode=rw`, requiring existing file. **No signed closure acknowledgment was issued.**

## Witness 1 — actual Neo4j server transaction across client SIGKILL

Script: `tests/probe_trace_physical_observer_526.py`

- Server version **5.26.26**, independently observed `neo4j-transaction-5` while worker was alive; abrupt worker exit **-9**.
- Independent observer saw same transaction ID still listed **6ms** after SIGKILL; **not listed** on observations at **132ms** and **258ms** after kill. Subsequent Neo4j health read succeeded.
- These are elapsed times *inside this one lab probe*, not p95/p99 or a cancellation deadline. Empty later listings are not a signed remote-completion attestation and cannot rearm a slot.
- Local raw invented-data witness: `/tmp/assistx-trace-physical-result-20261009.json`.

## Witness 2 — real Redis 7 state restart with physical Neo4j query

Script: `tests/probe_trace_physical_admission_526.py`

- Single-host durable SQLite cap=1 had one reservation before worker submission. Independent observer saw server-owned Neo4j `neo4j-transaction-16` running **before and at the actual disposable Redis restart**.
- The *nonpersistent lab Redis* was seeded with one synthetic active-state key. On restart the key was **absent**; this reproduces a real Redis memory-state loss while server-side Neo4j work remains active.
- Against the **surviving local SQLite ledger**, bounded threaded contender waves of **1, 3, 5 and 10** each received 100% `full` denials: **19 attempted replacements / 0 admitted**. The waves are concurrent *local threads*, not authenticated production API or distributed cross-host workers.
- Worker then exited via SIGKILL (**-9**). Local ledger occupancy remained **1** and an attempted successor still returned `full`. Subsequent exact transaction listings no longer contained the old ID. **The experiment did not reclaim the slot.**
- This verifies *one local ledger* can maintain conservative admission despite Redis losing state. It does not solve local SQLite snapshot rollback, file-copy split brain, inter-host ownership, signed receiver custody, or blackholed Bolt transport.
- Lab witness: `/tmp/assistx-trace-physical-redis-loss-20261009.json`.

## Witness 3 — administrator-directed independent Neo4j termination

Script: `tests/probe_trace_terminate_tx_526.py`

- Separate observer saw a live synthetic read as server-owned `neo4j-transaction-27`. It issued a **parameterized** `TERMINATE TRANSACTIONS $id` against that exact ID on the disposable Neo4j server.
- Neo4j returned `transactionId=neo4j-transaction-27`, `message=Transaction terminated.` The worker exited reporting `ClientError`; six subsequent observer listings did **not** show the same ID.
- This is stronger evidence than assuming driver timeout equals server cancellation, but the response is still **not an independently signed/attested terminal event bound to distributed epoch/token, nor a fleet-wide no-overlap proof**.
- Lab witness: `/tmp/assistx-trace-physical-terminate-20261009.json`.

## Regression / safety evidence

`python3 -m pytest -q tests/test_trace_durable_ledger_research.py tests/test_trace_physical_probe_guardrails.py` on x1-370 => **36 passed**. Includes prior 18 durable-ledger / negative-control tests and 18 parameterized probe preflight and bounded contender cases. The existing CI workflow is amended to run all **36 CPU-only tests** on exact PR head; it **does not** run live Docker/Neo4j and must never be represented as physical acceptance.

The source-only CI also tests false-positive scenarios: copying a SQLite ledger allows two independent copies to admit globally; a signer can sign an incorrect Neo4j termination statement; a valid signature alone is not truth. No producer, stored trace, execution command or model provider is exercised.

## Remaining hard NO-GO under #148

1. **Distributed durable single authority:** independent host/ledger copies, store rollback and epoch cutover can rearm occupied capacity; no consensus or independent globally durable authority exists. Repeat across real isolated **multiple API workers/hosts** with induced Redis failover and cross-host split brain. Fail closed whenever ownership is ambiguous.
2. **Receiver-owned transaction custody:** record the precise server transaction ID before dispatch; an independent reader with separately protected Ed25519 signing key must verify the same ID has reached an actually terminal state, bind a receipt to (epoch, token, query ref, graph instance, tx ID, observation), and prevent fabricated, replayed, nonterminal or wrong-graph acknowledgments. Mere `SHOW TRANSACTIONS` absence and `TERMINATE` response do not prove a globally signed completion event.
3. **Network blackhole/cancellation proof:** isolate a network fault at a disposable proxy to determine whether a severed or stalled Bolt transport leaves physical query work after the 4s transaction timeout. Present wall vs server timelines and an independently witnessed closure; no automatic TTL reaper. This turn tested SIGKILL and Redis restart, **not blackhole/failover**.
4. **Authenticated production-like API staging:** 1/3/5/10 simultaneous requests against source-owned endpoint, rate+in-flight caps, NAT identity/fairness, malformed/unauth zero-budget behavior, query PLAN/DB hits, driver p95/p99, Redis downtime/rollback, manual authenticated browser and operator rollback. Separate ingress spoof/header provenance #149 remains open.
5. **Merge/release authority:** Existing draft #203 combined CI is green but does not include this research. Do not wire these probes into production, assign key custody to worker, enable trace paging, reopen legacy full-detail, or deploy a controller based on these results. #148 must remain OPEN.

**Disposition:** real single-host cancellation/occupancy failure modes are now witnessed, and cautious denial during local Redis state loss has repeatable evidence. **Research PR remains draft. Strict fleet-wide physical fence is not proven.**
