# Research: Redis process-identity fencing during synthetic trace reads

Date: 2026-10-09. Status: **research only, NEVER production-authoritative**.

## Why this exists

[#148](https://github.com/scottjoyner/auto-assist/issues/148) demonstrates a strict physical-capacity failure: a Redis lease can disappear on expiry/restart/failover while an old graph read remains physically active. A successor then receives a nominal slot even though capacity is still occupied. Existing isolated cancellation evidence from [#174](https://github.com/scottjoyner/auto-assist/pull/174) stops a real **async** Neo4j query on an in-process fake renewal failure, but does not establish real distributed recovery authority.

This patch adds an **optional** `redis_run_id_pin` to the **unwired research-only** staging adapter. The run-id must be a 40-character lowercase hex identifier obtained from `INFO server` and pinned externally after an independently verified quiescent state. This module *does not* issue or authenticate such external attestations, and no live service uses it.

## Fail-closed negative acceptance

1. Reject absent/mismatched/malformed Redis run-id identity **before** acquiring a slot. Query never starts.
2. Reject changed Redis identity between precheck and atomic Redis-Lua admission. Best-effort exact-token release, but never run query.
3. On heartbeat, reject changed Redis identity and attempt cooperative transport cancellation. Never return a result on renewal/identity failure.
4. Check Redis identity before **and after** cleanup/release. Suppress successful responses if the instance changed.
5. Preserve old unpinned behavior only for backwards-compatible isolated research fixtures; **the unpinned path is NOT physically safe**.
6. Fail on Redis `INFO` outage. Never translate an instance-change or store outage into a 429 quota exhaustion success.

CI checks these failures with synthetic Redis, without sockets, external keys, Redis/Neo4j writes, or production route wiring.

## Explicit unsolved boundaries / falsifiers

- The read-only `INFO server` check is not atomic with `EVAL`, physical query submission, or driver completion. A Redis failover can occur in the intervals. A last successful check does not prove a cancelled graph query ended.
- A caller can supply an arbitrary new run-id after failover; this research adapter does **not** validate an independently signed operator/witness approval, nor does it enforce one epoch across all workers. Therefore **no safe re-admission after crash is proven**.
- The process run-id can change on promotion, restart, proxy reconfiguration or reconnect. Conservatively this denies admission; it is not a distributed leadership protocol.
- Redis Cluster, AOF persistence, replica split-brain, connection pool pinning and server identity behind load balancers have not been tested.
- A remote graph query may survive client cancellation or network blackhole. Physical capacity cannot be reclaimed on HTTP 503, client timeout, watchdog cancellation, or Redis lease expiry alone.
- No real authenticated multiworker 1/3/5/10 load, network partition, Redis failover/restart, historical index DB-hit p95/p99, operator rollback or signed cancellation receipt is established.
- No actual Redis/Neo4j/FastAPI deployment, config mutation, model admission, trace read or provider calls occur in this PR.

## Promotion gate

Only consider production routing after **all** independent conditions are witnessed: #149 ingress identity, receiver-owned operator-signed generation/epoch and durable authority on Redis-loss restart, authenticated real cancellation acknowledgment or conservative retained capacity, isolated Redis failover/lost-ack trials, physical 1/3/5/10 authenticated API+Neo4j tests under budgets, preserved #156 credential custody, #145 exact release CI and rollback sign-off.

Keep #148 **OPEN** and release **NO-GO**. The result is a negative-safety research improvement, not proof of strict physical-concurrency fencing.
