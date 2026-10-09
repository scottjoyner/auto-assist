# AssistX #148 — three-host witness, real fleet requests and rollback falsification

**2026-10-09 — SOURCE AND ISOLATED PHYSICAL HOST RESEARCH ONLY.** Parent draft [#227](https://github.com/scottjoyner/auto-assist/pull/227). No runtime/API modules import the witness or client, and there is no admission release, graph query, persistent service, host network change, Redis/NAS modification or credential change.

## Test topology

Three distinct physical nodes connected by existing authenticated SSH:

- **x1-370:** synthetic admission request origin
- **xwing:** independent synthetic admission request origin
- **raspberrypi:** independent, *single* SQLite witness owner holding one deliberately nonexpiring capacity-one reservation

The only witness source on the Pi was copied into a disposable, owner-only `/tmp/assistx-threehost-witness-test-20261009` directory, mode 0700, script mode 0600. Initial source SHA-256 matched on x1 and Pi: `c4c48fd4115813ce39ecd560fd27e80ef69b5f8529a1c82332f706f9e0399096`. After tightening the response binding and CLI input validation, source SHA-256 matched again: `353f312c19a30efc46473d97da05e33b28019c59dda4edd28149ef9def4bee74`.

The Pi used Python 3.13 and SQLite 3.46.1. No new port or listener was created. The clients use strict SSH known-host checks with `BatchMode=yes`, three-second connection timeouts and no more than three concurrent SSH commands per origin. Commands are limited to the explicit disposable script path, approved Raspberry Pi target, pinned synthetic epoch `f8d7c2b1-7f44-4cd5-bba8-3325b9a4b073` and fictional graph ID `a` × 64. Witness storage uses no-create `mode=rw`, SQLite `BEGIN IMMEDIATE`, `synchronous=FULL`, 0600 file permissions, pinned epoch/graph, exclusive singleton, and monotonic sequence when the actual database survives.

### Physical test A — both requesters share the independent witness

- Initial origin x1-370: **ADMITTED**, sequence 1.
- Initial origin xwing: **FULL**, 0 admitted.
- With the slot still occupied, both nodes sent bounded waves of 1, 3, 5 and 10 requests, maximum parallelism 3 per origin: **38/38 additional requests denied FULL**, 0 replacements admitted.
- After tightening CLI input and reply binding, each origin sent an additional 3 requests: **6/6 denied FULL**. The client's `admitted` response requires a matching `epoch`, `graph_id`, `query_ref`, numeric sequence, active=1, UUIDv4 nonce, and 32-character token; an unexpected or malformed grant fails closed. The witness rejects shell-significant query IDs at the client before SSH.
- Overall across these bounded requests: **one grant, 45 FULL denials, zero other grants**. These are *synthetic admission* calls only, not Neo4j queries.
- Raspberry Pi witness finally reported `active=1, capacity=1, sequence=1`.

### Physical test B — deliberate SSH delivery failure

xwing attempted 10 requests with a process-local `ProxyCommand=/bin/false` override. **10/10 became authority-unavailable; zero admitted**, without changing the host network, router or Raspberry Pi service. This verifies denial on this controlled SSH failure, not a true bidirectional network partition or a long-duration management-plane outage.

### Physical test C — **rollback of the independent witness itself**

A **second, separately labeled** disposable Pi witness `/tmp/assistx-threehost-witness-test-20261009-rollback` was bootstrapped with epoch `d8cc2b0e-1ed1-4b2e-9a3b-70e5037b11cf`. A pre-admission snapshot was kept **only inside this disposable directory**. The first synthetic grant returned sequence 1. The old zero-reservation journal bytes were then restored to the same path, preserving the inode. A different request **received another ADMITTED grant, again sequence 1**, with a distinct token and nonce; the final SQLite state reported only one occupied slot because the first grant was forgotten.

**This is an actual rollback counterexample on the third physical node.** A server on a third machine is not safe if its own authority state is rollbackable. A signature or matching epoch would not prevent these duplicated authorizations. This is not evidence that two actual Neo4j queries overlapped, because no graph work was submitted.

## Source custody and tests

- `tests/probe_trace_third_node_witness.py`: standalone, disposable Raspberry Pi witness with no release method and no production import.
- `tests/probe_trace_third_node_client.py`: bounded strict SSH-only requester; no local authority copy or automatic failover.
- `tests/test_trace_third_node_witness_research.py`: 1/3/5/10 threads, multiple processes, restart, bad path, missing/clone owner, same-inode rollback **expected counterexample**.
- `tests/test_trace_third_node_client_research.py`: deliberate SSH outage, wrong source host, epoch/query/graph reply mismatch, malformed input/shell metacharacter denial, valid synthetic bound grant.
- The existing GitHub `trace-durable-ledger-research` workflow now includes the new two modules, but **hosted CI runs synthetic local test fixtures, not physical SSH to these machines**.

The earlier parent [#227](https://github.com/scottjoyner/auto-assist/pull/227) passed exact-head full CI **1,030 passed / 59 deselected, recovery-canary PASS**, and previously proved copied x1/xwing authorities each granted sequence 1.

## What this does NOT solve

1. **No consensus or quorum:** the Pi is a single point of authority and failure. Loss of its SSH, disk, clock, or identity must deny work. A replicated Pi image is **not** a second authority.
2. **No externally protected monotonic commit:** the Pi's **own** rollback test reissued sequence 1. Production requires independent, durable and rollback-resistant fencing/term authority, not a client-supplied high-water mark or a copied journal. Multiple nodes must agree on a unique active leader with provably fenced old leaders; SQLite files are not consensus.
3. **No production receiver trust:** SSH host-key verification binds this lab connection to a known host, but does not implement an operator-managed admission signing key, key rotation, revocation or global consumed nonce store. Parent #224's receiver evidence remains a separate research contract.
4. **No real query or lifecycle release:** none of these calls executed Neo4j, Redis, graph cancellation or physical query admission. There is still no legitimate receipt-to-slot-release integration, authenticated endpoint, workload fairness, driver p95/p99 or production-safe rollback.
5. **#149 ingress is separate:** real Caddy/Tailscale forged-header negatives, per-role privacy, operator review and device/browser readiness remain open.

## Release disposition

**#148 and #149 stay OPEN.** This is research-only, DRAFT, and must never be used to authorize live trace query concurrency or automatically release a slot.

The narrow next engineering acceptance is a third-party **quorum/consensus fencing design** (or proven independent append-only monotonic authority), with real two-origin partitions/rollbacks and witnessed Neo4j active-query overlap checks; failure to assemble quorum must deny new admission, not promote a local copy.
