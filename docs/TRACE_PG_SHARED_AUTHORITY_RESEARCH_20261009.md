# AssistX distributed-read admission: shared PostgreSQL primary experiment (2026-10-09)

**Status: RESEARCH ONLY; #148 OPEN; #149 OPEN; NO PRODUCTION DEPLOYMENT.**

This work is stacked on draft [#211](https://github.com/scottjoyner/auto-assist/pull/211), whose exact-head integrated CI had **946 passed / 59 deselected, recovery canary PASS, physical guard and paged trace workflows PASS**. No research module is imported by a live API handler. It does **not** authorize trace activation.

## Narrow physical hypothesis

A single centralized PostgreSQL 17 authority can serialize non-expiring shared occupancy for independent worker processes, even when they open separate client connections. It must refuse stale external epochs, database-state disappearance, and absent authorization for closure. This is NOT distributed quorum consensus: one authoritative instance is a single point of unavailability and cannot claim safe automatic takeover by an arbitrary restored copy.

## Disposable actual experiment on x1-370

The named test-only `postgres:17-alpine` Docker image, cached locally, was started with:
- Container `assistx-trace-authority-pg-20261009`; isolated **internal** bridge `assistx-trace-authority-net-20261009` and container IP 172.23.0.2.
- NO published ports, network peers, host bind mounts or persistent volumes. 1 CPU / 512 MiB / 128 PIDs. Temporary PGDATA tmpfs (256 MiB). Existing Docker SSD was ~96% used; test made no bulk or NAS writes.
- `POSTGRES_HOST_AUTH_METHOD=trust` on the **disposable inaccessible-from-host-routes research network only**. This is explicitly **NOT** a production authentication model. Worker processes use the database superuser and can bypass SQL admission through direct write/DELETE; therefore no security claim from this prototype alone.
- The experiment requires exact named Docker target, cached image, internal-only bridge, no published ports/mounts, caps, an externally pinned UUIDv4 epoch and `ASSISTX_TRACE_PG_DISPOSABLE_RESEARCH=1`. Any other Docker target or DSN is refused.

Manually bootstrapped once with an explicit externally pinned epoch, capacity 3, and non-expiring reservations. Admission uses a PostgreSQL `SELECT ... FOR UPDATE` metadata-row lock, a bounded server statement timeout, epoch/schema comparison, atomic reservation INSERT and no TTL or crash-triggered release. An initial trial showed `FOR UPDATE NOWAIT` caused excessive contention rejection and was corrected to a bounded lock wait before successful rerun.

### Physically witnessed 1/3/5/10 separate worker processes

```sh
PYTHONPATH=src ASSISTX_TRACE_PG_DISPOSABLE_RESEARCH=1 \
 /tmp/assistx-pg-authority-venv-20261009/bin/python tests/probe_trace_pg_authority.py
```

| Parallel workers | Shared authority admitted | Denied | Actual PostgreSQL active server queries | Occupancy retained after workers exited | Occupancy after exact signed releases |
|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 1 | 1 | 0 |
| 3 | 3 | 0 | 3 | 3 | 0 |
| 5 | 3 | 2 | 3 | 3 | 0 |
| 10 | 3 | 7 | 3 | 3 | 0 |

The worker processes executed real `SELECT pg_sleep` queries on the isolated database while an independent connection read `pg_stat_activity`. Release occurred **only after** those server queries disappeared. Wrong epoch and duplicate bootstrap were refused; malformed/forged Ed25519 signatures and replay attempts did not free slots. There was no automatic expiration. This validates a shared-primary, cross-process SERIALIZED occupancy invariant, not cross-host/cluster physical Neo4j concurrency.

### Real authority-state-loss negative

After reserving a token under an externally pinned epoch, **only the disposable Postgres container** was restarted. Its tmpfs PGDATA was lost; there was no automatic bootstrap or schema regeneration. An existing authority instance returned `inspect=None` and `acquire(...)` returned `Admission(None,None,"unavailable")`; a newly constructed authority rejected `AUTHORITY_NOT_VALIDATED`. Zero `research_trace_*` tables were recreated. Thus **lost authority state did not silently reopen capacity**, unlike the previous soft Redis hash design.

### Offline reproducible guards

```sh
python3 -m pytest -q tests/test_trace_pg_authority_guards.py \
    tests/test_trace_physical_probe_guards.py \
    tests/test_trace_durable_ledger_research.py
# 51 PASS on x1-370
```

This includes opt-in denial, forbidden host/public ports, extra networks, bind mounts, foreign images/IPs, resource caps, external epoch validation, malformed authorization and physical witness safety guards. Hosted CI executes **only** these offline guards and never creates a database or accesses production.

## Remaining security failures — explicit hard NO-GO

1. **Database-side enforcement and privilege separation:** the test currently connects as PostgreSQL superuser under isolated trust authentication. A malicious worker can bypass all client-side locking, release or reinitialize the ledger. Next slice must move capacity/epoch enforcement into SECURITY DEFINER stored procedures, use separate least-privileged authenticated worker and verifier roles, and ensure workers cannot access or mint verifier private keys.
2. **Cluster/quorum/monotonic epoch:** a single PostgreSQL primary is not HA or a quorum. No synchronous failover, replica promotion, backup rollback, external monotonic epoch authority, witness consensus, or network partition fencing was exercised. A destroyed PGDATA must remain hard-denied unless an independently audited recovery decision proves no old server query survives.
3. **Neo4j's physical transaction identity:** the PostgreSQL probe measures actual PostgreSQL server activity. Earlier [#209](https://github.com/scottjoyner/auto-assist/pull/209) separately witnessed actual Neo4j 5.26 SIGKILL and stalled-Bolt behavior. This iteration did **not** wire the PostgreSQL reservation to a server-owned Neo4j transaction or cryptographically bind its identity. Worker-controlled query comments and self-reported closure remain insufficient.
4. **Authenticated multiworker API and ingress:** no live FastAPI/trace handler, user RBAC, rate/window budget, trusted proxy #149, Caddy header fencing or device/browser traffic was authorized or exercised here.
5. **Witness custody:** ephemeral in-process Ed25519 signer is useful for falsifying forged signatures, not a production independently owned signed server-termination receipt. The signing key must be strictly external to workers and released only after independently observed remote closure, including restart/partition ambiguity.
6. **Deployment:** production flags stay OFF and all research files are unwired. No graph, credentials, production Postgres, production Redis, NAS, Tailscale, or operator services modified.

**Engineering conclusion:** A shared transactional authority can enforce simultaneous admission for cooperative workers, and losing authority state can be safely denied. Secure database-enforced role separation and production independent Neo4j witnessing are the next gates; do NOT mark #148 closed or merge/ship solely from these tests.
