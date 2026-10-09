# AssistX physical-query admission — isolated Neo4j 5.26.30 research (2026-10-09)

**Disposition: physically observed SINGLE-HOST research, not a global production fence.**
Issue [#148](https://github.com/scottjoyner/auto-assist/issues/148) remains **OPEN / NO-GO**. This research branch stacks on [#186](https://github.com/scottjoyner/auto-assist/pull/186), containing the persistent local-only SQLite reservation prototype. It is **not imported by any API route** and does not change production Neo4j, Redis, NAS, AssistX services, provider routing, Tailscale, auth, or data.

## Preregistered question and safety scope

Can a single surviving local SQLite ledger deny new remote graph reads across process crashes, and can an **independent Neo4j transaction observer** with a separately held ephemeral signing key prevent capacity release until the actual server transaction disappears? Does a simulated silent Bolt transport blackhole demonstrate why process death or `TERMINATE TRANSACTIONS` success is insufficient?

Only a named, inspected, disposable `neo4j:5.26-enterprise` image was allowed:
- Docker container **`assistx-physical-trace-probe-20261009`**, isolated **internal** Docker bridge `assistx-physical-trace-net-20261009`; container-only IP **172.23.0.2**; no published ports or bind/persistent volumes, no prod data.
- 1.0 CPU, 1800 MiB memory; ephemeral `/data` tmpfs capped at 350 MiB and `/logs` at 64 MiB. Docker cached image; **no new image download**. Host Docker filesystem was ~96% utilized and was not used for bulk graph writes.
- `NEO4J_AUTH=none` applies **only** to that network-isolated disposable fixture. Graph was initially offline because `server.databases.read_only` unexpectedly listed `neo4j`; the setting was cleared **inside the disposable instance only** and startup reverified. Production auth/config untouched.
- Source scripts reject wrong container/image, host networking, non-internal bridge, extra networks, exposed port, host bind mount, unexpected IP, excess resource limits and any run without `ASSISTX_TRACE_DISPOSABLE_PHYSICAL_PROBE=1`. All synthetic ledger state was under disposable `/tmp/assistx-trace-ledger-test-*`; no key material persisted.

## Physical probe A: SIGKILL + independent witness + 1/3/5/10 contenders

Command, in isolated worktree with explicit opt-in:

```sh
PYTHONPATH=src ASSISTX_TRACE_DISPOSABLE_PHYSICAL_PROBE=1 \
  python3 tests/probe_trace_physical_lifecycle_526.py
```

A real expensive, read-only `UNWIND` statement was observed by independent `SHOW TRANSACTIONS` as `neo4j-transaction-22`. Query comments included a unique admission token, and the independent witness bound **transaction ID + token + epoch + query reference** at first physical sighting. The worker did **not** possess the observer's private key.

Observed:
- A prematurely requested closure receipt was **refused** while the statement remained active. A forged wrong-token binding was **refused**, despite the caller supplying a valid observed transaction ID.
- The worker was killed with SIGKILL (exit **-9**). Its non-expiring SQLite reservation stayed occupied; a would-be successor was denied.
- The independent observer eventually found the original server transaction absent on **two separate snapshots**, signed a closure receipt for the previously bound identity, and the ledger released one exact token. Replay was rejected; only then was a successor admitted.

Distinct simultaneous subprocesses competed for a cap of 3 on one surviving local SQLite ledger. These were **real, open Neo4j transactions**, verified through a separate server-side `SHOW TRANSACTIONS` query while workers remained active. No actual graph node data was needed.

| Attempts | Admitted | Denied | Server-observed open transactions | Persisted SQLite occupancy |
|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 1 | 1 |
| 3 | 3 | 0 | 3 | 3 |
| 5 | 3 | 2 | 3 | 3 |
| 10 | 3 | 7 | 3 | 3 |

## Physical probe B: silent Bolt transport blackhole

```sh
PYTHONPATH=src ASSISTX_TRACE_DISPOSABLE_PHYSICAL_PROBE=1 \
  python3 tests/probe_trace_transport_blackhole_526.py
```

A localhost-only, disposable TCP relay opened a real Bolt connection to the already-inspected instance. The relay deliberately stopped transmitting in **both directions while retaining the upstream socket**. The read-only statement carried the uniquely bound ledger admission token and had a **20-second Neo4j transaction deadline**.

After independent observation of `neo4j-transaction-33`, the proxy blackholed traffic and the client worker was killed with SIGKILL (**-9**). Two independent `SHOW TRANSACTIONS` checks nevertheless showed the server transaction **still physically active**. The shared ledger rejected a successor.

An independent admin connection then issued `TERMINATE TRANSACTIONS` for **that exact witnessed transaction ID** on this disposable instance. Neo4j replied **`Transaction terminated.`**, but the independent witness still saw the transaction and **correctly refused to sign a receipt**; the ledger **remained full**. Only after deliberately closing the stalled relay's socket did the witness observe the transaction disappear, sign the matching bound receipt, release exactly one reservation, and permit a successor.

**Crucial finding:** A successful `TERMINATE TRANSACTIONS` command response does not itself establish physical closure when the Bolt transport is blackholed. A worker exit code, Python exception, Redis token expiry or brief empty browser UI are even weaker evidence. Release requires authoritative observed server-transaction disappearance, retained identity, and fail-closed uncertainty handling.

## Additional offline guards and CI

```sh
python3 -m pytest -q \
  tests/test_trace_durable_ledger_research.py \
  tests/test_trace_physical_probe_guards.py
# 33 passed on x1-370
```

These include genuine local multiprocess SQLite serialization, crash retention, missing ledger, inode replacement, signed receipt mismatch/replay refusal, two expected-negative split-brain / dishonest-signer controls, and opt-in/Docker identity/resource/network prohibitions. A separate exact-head GitHub workflow runs **only offline synthetic guards**. Hosted CI does not automatically start containers, run the physical probes or access production.

## What remains unproven — explicit hard NO-GO

1. **Global durability and distributed consensus:** SQLite on one host does **not** prevent two independent copies, reimaging, rollback or cross-node split-brain from each admitting capacity. The previous Redis hash or expiring token also fails on Redis state loss. A separately owned durable, quorum-consistent global admission authority with monotonic epochs, escrow/recovery and operator rollback must be designed and physically tested.
2. **Server-owned transaction-to-token binding:** These research comments and ephemeral witness are **not tamperproof against a compromised worker**. A worker might mislabel queries or create unmarked second transactions. An admission-capable production implementation requires a verifiable, independently owned transaction lifecycle and signer key custody; a self-reported close callback, false signed assertion or mere two snapshots after a partition is not hard proof.
3. **Full network and API topology:** Here the TCP blackhole occurred on one loopback relay to a single disposable Neo4j server. No authenticated multiworker FastAPI route, live reverse proxy, NAT fairness, Redis hard failover, Neo4j cluster reboot/partition, post-restart epoch reconciliation or multi-node admission was validated.
4. **Other release gates:** trusted ingress [#149](https://github.com/scottjoyner/auto-assist/issues/149), per-role trace privacy and legacy detail, authenticated browser/mobile and driver p95/p99, source history/credentials, the normal full production CI and operator-approved rollback remain separate.
5. **No runtime integration:** Both probes and local SQLite ledger remain **research only** and do not confer production dispatch, query, graph write, provider, deletion or installation authority.

**Recommended next implementation:** independent distributed reservation authority backed by a durable monotonic epoch plus source-owned, server-transaction identification and an independently controlled termination-witness process. Make uncertain state permanently deny capacity until a verified closure or explicit human recovery under audit. Only after isolated cross-node/cluster failures succeed should the new trace routes be wired behind default-OFF feature controls.

This document records observations and does **not** promote PRs #186 or its research children to production.
