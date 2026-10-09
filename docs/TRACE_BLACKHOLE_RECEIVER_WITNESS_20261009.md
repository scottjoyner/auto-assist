# AssistX #148 — Real Bolt transport blackhole and isolated receiver signing

**2026-10-09 — RESEARCH ONLY. No API routing/production admission, no merge/release authorization.**
This branch is a draft on green research/integration [#216](https://github.com/scottjoyner/auto-assist/pull/216). It exercises **only** synthetic Cypher in a short-lived explicitly labeled Neo4j Enterprise 5.26.26 container on an internal-only Docker network, with **1 CPU / 2 GiB memory / no published ports / no bind mounts**. No firewall rules, NAS, production graph, credentials, model providers or fleet Redis were accessed.

## Why the test matters

A worker can still have an open Bolt connection yet be unable to receive any response. A remote graph query may continue doing physical work during that time. A 4-second HTTP/driver timeout and even a correctly signed message are **not** sufficient reasons to admit another query.

### A. Blackholed TCP relay and independent Neo4j termination

`tests/probe_trace_bolt_blackhole_526.py` verifies the exact disposable Neo4j image, label, isolated network and caps via `guarded_endpoint()`. A Python relay binds only to `127.0.0.1` on an ephemeral port and connects to that validated Docker-network Bolt socket. After the server's own transaction ID is independently visible in `SHOW TRANSACTIONS`, the relay stops forwarding both directions without closing the worker's connection. No host firewall changes or external services.

Actual x1-370 observation:
- Client worker retained an open, blackholed Bolt connection while Neo4j server transaction **neo4j-transaction-10** remained observable from a separate driver.
- Durable local SQLite cap=1 returned **`full`** to a second query during the blackhole.
- The direct Neo4j observer issued a parameterized `TERMINATE TRANSACTIONS $id` to the exact preobserved server ID; response matched `neo4j-transaction-10` and `Transaction terminated.`.
- Six subsequent exact-ID server transaction listings were absent, but the worker was **still alive behind the blackholed stream**.
- Admission remained **`full`** and ledger occupancy remained 1 **after** the termination command. The relay was unfrozen for cleanup only; no receipt was signed or capacity reclaimed.
- Initial local attempt failed only on an early 200 ms proxy `accept` timeout, prior to any observed server query; the harness was corrected to bounded 10-second startup accept. Only the later completed run constitutes blackhole evidence.
- Result JSON: `/tmp/assistx-trace-blackhole-20261009-observed-3.json`.

### B. Separate receiver process with an ephemeral key

`tests/probe_trace_receiver_custody_526.py` repeats the blackholed physical query but spawns an **independent process** that:
1. Independently validates the Docker graph target and obtains its container identity (not a client-submitted graph URI).
2. Directly witnesses a single real server transaction matching the synthetic query marker and previously observed transaction ID.
3. Issues `TERMINATE TRANSACTIONS $id` and demands an exact ID plus the server message `Transaction terminated.`.
4. Independently samples six subsequent transaction listings and refuses to sign if the old ID remains present.
5. Generates an **ephemeral Ed25519 private key inside the receiver process**, signs a schema-strict evidence record, and returns only the public key, signature, and record to the parent.

The record binds **epoch, admission token, query reference, unique receiver nonce, exact graph container identity, Neo4j server transaction ID, observed-running-before status, termination acknowledgment and six after-observations**. `src/assistx/trace_receiver_evidence_research.py` verifies the canonical signature and these exact fields; wrong graph/transaction/epoch/token/nonce denies. A replay against a different expected nonce denies; replay against the same tuple **still verifies**, because there is **no durable consumed-nonce store** in this research.

Actual x1-370 observation:
- The receiver observed `neo4j-transaction-24` during a transport blackhole; the query worker remained alive awaiting the blocked stream.
- Separate receiver signed a valid bound laboratory receipt, independently verified in the parent; wrong expected request nonce denied.
- A different unused local ledger public key deliberately ensured this v2 research signature **cannot** unlock the existing v1 SQLite ledger.
- **Durable ledger stayed occupied=1**, and subsequent admission still returned `full` after successful signature verification. No automatic release, worker-directed signing, or production key use.
- Result JSON: `/tmp/assistx-trace-receiver-20261009-1.json`.

## Test/cleanup evidence

`python3 -m pytest -q tests/test_trace_receiver_evidence_research.py` passed **27/27 offline tests** including malformed/changed receipts, 6 exact-binding variants, nonce substitution, wrong key, and no automatic-release contract. Existing probe guardrails also passed, with additional loopback-only and non-disposable-target negatives in this branch. Exact-head GitHub CI runs **offline** research tests only; Docker is never started in hosted CI.

Before cleanup, `docker inspect` identified the exact disposable container label, expected internal network, empty port bindings, and network membership. The single Neo4j container and internal Docker network were removed. Neither of the two probes accessed production services or data. No credentials or signing private key were written to Git.

## Critical limitations — #148 is still a production NO-GO

- **Truth versus signature:** this attestation is a signed *receiver observation*, not a universally verified terminal event. The receiving process may still be wrong, compromised, unable to witness all transactions or suffer a server-restart/ABA identity problem. A signature does not make the contents true.
- **Ephemeral key custody:** the private key was isolated to one disposable process, but there is no durable receiver-owned key escrow, pinning, revocation, rotation, attester attestation, or independently configured operator trust anchor. The verifier received its lab public key along with the receipt, which is **not a trusted production key distribution channel**.
- **Replay/rollback:** nonce mismatch is denied only if a trusted caller pins the expected value. There is no durable consumed-nonce register, global query ownership, rollback-proof ledger or independent multi-host consensus; copied ledger/signer counterexamples remain.
- **Physical deadline:** the test intentionally terminated one real query after freezing its stream; it did *not* prove Neo4j's 4-second deadline by itself ends every query during a prolonged network blackhole, server failover or complete management-plane outage.
- **Admission interface:** no endpoint, middleware, Redis lease, graph routing, production authorization or distributed capacity is wired to the receiver. The existing single-host SQLite ledger remains nonexpiring, and the test does **not** call `acknowledge_remote_closure`.
- **Release gate:** authenticated multiworker 1/3/5/10 HTTP+graph end-to-end staging, durable epoch fencing, cross-host crash/split-brain/rollback, operator-owned signing keys, physical trusted ingress #149, graph-instance failover, p95/p99 and rollout/rollback all remain outstanding.

**Disposition:** signed receiver-custody separation and real transport-blackhole observability are demonstrated on disposable isolated infrastructure, with conservative slot retention. Keep both #148 and the PR DRAFT/NO-GO.
