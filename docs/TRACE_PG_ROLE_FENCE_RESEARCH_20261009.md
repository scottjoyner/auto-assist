# AssistX query-admission role fence: isolated PostgreSQL SCRAM experiment

**2026-10-09 | RESEARCH ONLY | #148 NO-GO | no live API/Neo4j deployment**

## Objective and provenance

This child of [draft #213](https://github.com/scottjoyner/auto-assist/pull/213) closes the earlier *disposable research* superuser credential bypass: enforce the global admission cap **inside PostgreSQL** and deny worker read/alter/delete/release access to authoritative state. The operator/test-harness still has Docker and database bootstrap authority; no quorum/failover claim is made.

Source: `research/trace_pg_privilege_fence.sql`. Probe: `tests/probe_trace_pg_privileged_roles.py`. Offline negatives: `tests/test_trace_pg_privilege_guards.py`.

## Physical isolated fixture

- x1-370, cached `postgres:17-alpine` (server reported PostgreSQL 17.11 in earlier primary experiment), `assistx-trace-privilege-pg-20261009`, named internal bridge `assistx-trace-privilege-net-20261009`, no published ports, no bind/persistent volumes, temporary PGDATA, 1 CPU / 512 MiB.
- **SCRAM-SHA-256** for TCP database authentication. Bootstrap role (`postgres`) password was generated in a temporary local 0600 file, not shown or committed. Distinct worker and verifier passwords were also independently generated and kept only on x1. Credentials and Docker resources were deleted after testing.
- One explicit pinned UUID4 genesis. A `SECURITY DEFINER` `admit` function locks the metadata row with `FOR UPDATE`, checks the schema and epoch, and atomically inserts non-expiring reservations up to capacity **3**. Admission is committed BEFORE physical work begins, avoiding serialization of the entire query runtime. A previous failure (two contenders cancelled after waiting behind an open query transaction) was fixed and rerun.
- PostgreSQL PUBLIC receives **no schema/table/function privileges**. The worker role receives schema USAGE and EXECUTE for `admit`/`inspect` only. The verifier role receives schema USAGE and EXECUTE for `inspect`/`release_exact` only. Worker has no table SELECT/INSERT/UPDATE/DELETE, cannot create/rearm/drop authority schema, and cannot call the verifier function.
- **The release SQL function does not validate Ed25519 itself.** The verifier process must independently validate a cryptographically bound *actual Neo4j physical-termination witness* before calling it. That independent service and its private signing-key custody have not yet been implemented.

## Physically witnessed admission and privilege negatives

Using real independently spawned worker processes and **real active PostgreSQL server-side `pg_sleep` read statements**, observed via `pg_stat_activity`:

| Concurrent attempts | SQL-level accepted | Denied | Independently observed active PostgreSQL reads |
|---:|---:|---:|---:|
| 1 | 1 | 0 | 1 |
| 3 | 3 | 0 | 3 |
| 5 | 3 | 2 | 3 |
| 10 | 3 | 7 | 3 |

The worker role was independently denied each direct SQL operation: table SELECT, reservation DELETE, capacity UPDATE, authority-table DROP, and `release_exact`. A verifier password could not authenticate as the worker. Unknown epoch was denied **inside the PostgreSQL procedure**. Valid verifier-role release was accepted after research query completion, and replay release returned false.

**Warning:** verifier release in this test was controlled by the test harness after PG activity ended; it was **not** authorized by the real independent Neo4j physical-witness service from research [#209](https://github.com/scottjoyner/auto-assist/pull/209). The SQL cap enforces occupancy among cooperative SQL callers; it does not force a malicious graph-client to acquire admission before executing an unrelated Neo4j query.

## Actual xwing remote ingress observation

The x1 host could connect to xwing by SSH. A **temporary SSH reverse tunnel** exposed only `127.0.0.1:65432` on xwing to the isolated PostgreSQL instance. A temporary Python 3.12 environment with psycopg 3.3.6 was installed for the test and removed afterward.

An **unauthenticated xwing connection** through that tunnel was rejected by SCRAM with a missing-password error. Authenticated worker credential transfer was blocked, so a two-host **admitted** concurrency run **was not performed**. Do not infer successful distributed admission across two physical hosts from the multiple-process x1 result.

The tunnel was terminated, xwing's test venv removed, local ephemeral credential files removed, and Docker test container/network stopped and removed. No resident services or production credentials changed. Docker storage remained capacity-constrained; no bulk writes.

## Offline safety and CI

```sh
PYTHONPATH=src:tests /tmp/assistx-pg-authority-venv-20261009/bin/python -m pytest -q \
    tests/test_trace_pg_privilege_guards.py tests/test_trace_pg_authority_guards.py
# 35 passed on x1-370
```

The dedicated exact-head GitHub workflow executes these **offline-only** fixture/SQL guard tests and does not launch Postgres/Docker or transmit secrets. Full repository CI and recovery canary remain separate gates. An earlier shared-primary branch passed **51/51** tests and physically demonstrated that deleting tmpfs PGDATA on container restart caused old authority to fail closed and never auto-recreate tables. That state-loss test was **not rerun** in this role-separated experiment due to an execution restriction; no new confirmation of the role schema after destructive restart is claimed.

## Still required to close physical #148

1. **Independent witness custody and server-owned transaction binding:** trusted verifier service that sees and reliably binds an actual Neo4j server transaction to exact epoch/token/query reference **without trusting worker-controlled comments**, signs only after authoritative query disappearance across rollback/cluster restarts/partitions, and does not leak signing/verifier DB credentials to workers.
2. **Global consensus and durable fenced recovery:** single PostgreSQL primary is a serial authority but not fault-tolerant distributed consensus. Prove safe backup rollback, synchronous quorum replication, elected leader, monotonic external epochs, split-brain quarantine, and fail-closed loss of receipt/database state before a successor can run.
3. **True two-host authenticated acceptance:** provision a least-privileged *test-only* worker credential to an isolated xwing process using approved custody, run x1/xwing simultaneous 1/3/5/10 admissions through a loopback-only authenticated tunnel, then test leader crash and network partition.
4. **Production API, ingress, load budgets:** no actual FastAPI graph handler, Neo4j server function, PII/role scope, Caddy/Tailscale #149, true cross-worker graph load or p95/p99 test was wired here.
5. **No activation:** All changes remain research-only and unwired, no production data/Redis/Neo4j/PG/AssistX/NAS/SSH service changes.

**Disposition:** role-privilege bypass of the *prototype* is fixed and physically demonstrated; globally durable, independently witnessed Neo4j admission remains a NO-GO for production. Never promote on CI or SQL synthetic acceptance alone.
