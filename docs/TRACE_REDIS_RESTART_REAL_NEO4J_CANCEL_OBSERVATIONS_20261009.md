# Redis restart while a real Neo4j read is active — isolated acceptance

**Date:** 2026-10-09 UTC. **Status:** validated isolated control, *not* production release acceptance. Follow-on to [#148](https://github.com/scottjoyner/auto-assist/issues/148), [research #174](https://github.com/scottjoyner/auto-assist/pull/174), and [#185](https://github.com/scottjoyner/auto-assist/pull/185).

## Question and preregistered boundaries

Do real Redis server restart and loss of its in-flight slot, while a Neo4j 5.26 **read-only synthetic calculation** is in progress, cause the unwired staging adapter to cancel the client async task, confirm the tagged remote transaction cleared, and deny the result?

This experiment **cannot** establish fleet-wide physical concurrency safety. It uses a single client container and an in-memory caller-provided Redis `run_id` pin; a new boot identity still requires an independently authorized, durable epoch/quiescence attestation that the adapter does not implement.

## Environment and source evidence

- x1-370, clean exact detached Git worktree; source commit `6b3ed0e7f2d97c5ab36001231689ad5b4ac079dc` reported **PASS**. The next hardening commits launch the three test images by immutable image IDs rather than tags and must be retested at their own exact head.
- New Docker `--internal` network with **no host published ports**. Fresh uniquely named Neo4j 5.26 Enterprise and Redis 7 Alpine containers, plus a read-only client. Network and container identity checks reject pre-existing or unexpected peers.
- Immutable image ID inputs are required; source HEAD and a clean checkout are checked before any Docker action. The scratch Neo4j server is restricted to 0.75 CPU / 2 GiB and tmpfs-only data/log/tmp. Redis: 0.25 CPU / 128 MiB, no persistence, non-root, readonly filesystem. Client: 0.4 CPU / 512 MiB, read-only mounts, no extra capabilities.
- Client loads only the pure, **unwired** research modules. It uses synthetic receiver key and principal, no production API/Neo4j/Redis/Nextcloud credentials. The Neo4j 5.26 arithmetic aggregation is read-only with `Query(..., timeout=3.0)`. The disposable scratch database's *system* configuration is initialized only inside its temporary instance to bring the synthetic database online.
- Real Redis Python client executes the existing atomic `ACQUIRE` / `RENEW` / `RELEASE` Lua contract; its `INFO server` `run_id` is pinned before starting the graph query. The host waits for the client marker that the tagged graph transaction is visible in `SHOW TRANSACTIONS`, then restarts **only the freshly created Redis container by immutable container ID**.
- All three test containers and the internal-only network are removed/cleaned by recorded identities in a `finally` block. CI only executes offline Docker-guard tests; it never runs this heavyweight canary automatically.

## Execution and observed result

1. First combined attempt was **inconclusive**: the Neo4j scratch admin health probe timed out during cold startup, and all test resources were cleaned up. A second attempt remained inconclusive because the shell was still queried before Neo4j had initialized.
2. Guard corrected to wait for Neo4j's own `Started.` marker before one bounded system-database check. The first query/restart attempt reached the client-cancellation witness stage but returned **inconclusive**, because the disposable `--rm` client could vanish before `docker wait`/logs were collected.
3. Client lifecycle corrected to retain immutable exit evidence until inspected, followed by cleanup using its immutable ID.
4. Exact `6b3ed0e...` successful run: **`REAL_REDIS_RESTART_NEO4J_CANCEL_SYNTHETIC_PASS`**; `NO_PRODUCTION_NETWORK_PORTS_OR_CREDENTIALS_USED`. The guarded test observed the running tagged Neo4j query before Redis restart, demanded the client success marker (which itself asserts async task cancellation, remote `SHOW TRANSACTIONS` clearance and output denial), and exited zero. No data from production graph was accessed.

This is a **real Redis + real Neo4j single-client cancellation** witness. Earlier #174 used *fake Redis* with real Neo4j; #185 used *real Redis* with a synthetic in-process query. Neither earlier control alone tested this combination.

## Remaining falsifiers / NO-GO conditions

- Redis process `run_id` is not a signed, durable, receiver-owned generation authorization. It cannot establish which physical remote reads are still running after leader promotion, failover, worker crash or an adversarial proxy reroute.
- Only the Neo4j **async** driver path was exercised, not the existing synchronous production `list_traces` route. This module is not wired to FastAPI.
- The client knows the exact server URI and has an isolated synthetic principal. Real authentication, proxy identity provenance and role/scope correctness remain open under #149.
- No 1/3/5/10 multiworker authenticated API + Neo4j contention, request abort/transport blackhole, Redis cluster failover/split brain, persistent lease ledger, physical capacity quarantine, per-route budget/p95/p99, or operator rollback has been demonstrated.
- Tracked public Git credential exposure #156 remains P0 and is independently release-blocking. No credential rotation or repository-history rewrite occurred.

**Disposition: retain #148 OPEN and production NO-GO.** The single-client real-Redis restart -> real-Neo4j cancellation experiment removes a meaningful staged-validation uncertainty, but does not yet authorize production execution or release.
