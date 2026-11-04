# Native Neo4j backup listener and RPO preflight

**November 3, 2026, x1-370. Research-only PASS for observed settings; production HOLD / NO-GO.**

## Physical evidence from the actual running service

The live image-pinned Neo4j Enterprise 5.26.26 container has the native
server backup service ENABLED. A read-only Neo4j SHOW SETTINGS query via
the running API confirms:
- server.backup.enabled = true
- server.backup.listen_address explicitly uses loopback with port 6362

The running container's kernel /proc/net/tcp6 also confirms one LISTEN
socket for port 6362, with an IPv4-mapped loopback bind. No host-published
backup 6362 binding exists. Other graph ports (host 7688 Bolt and 7475 HTTP)
remain published on all interfaces in the legacy Compose deployment.

The effective loopback binding on port 6362 means a backup client in an
ordinary separate Docker network namespace cannot directly connect to the
native listener. Opening 6362 on the host or reconfiguring Neo4j to
all-interface backup publishing is NOT recommended as a shortcut. A
separately owner-approved restricted backup client might run in the same
network namespace, but that requires a carefully reviewed access/egress
policy and resource budgets. THIS PR DOES NOT CREATE SUCH A CLIENT OR BACKUP.

The data filesystem had approximately 175 GiB free during the live
read-only check. A bounded two-level /data filename/metadata inventory
found one old dump (about 70 days of age) and no .backup chain artifact
or fresh .dump within the 24-hour research RPO. This scope does not
include off-host archives, directories outside /data or provider-managed
snapshots. It is not proof that external backups are absent.

The historical assistx ZSTD dump (868,045,968 bytes, 335 declared files)
was independently restored into an isolated 7.22 GiB scratch database,
passed a full offline consistency check, and deleted, as documented in
draft PR #542. Its historical age makes it unsuitable as a current
recovery point. Online backup protocol, auth and current restore remain
unverified.

## New immutable-image-pinned read-only preflight

scripts/read_only_neo4j_backup_readiness.py requires separate
immutable SHA256 image pins for both running Neo4j and AssistX API, plus a
bounded 1-168 hour RPO threshold. It runs only:
- fixed docker inspect neo4j and docker inspect assistx-api before
  and after, verifying image, running state, ID, startup time, bind mounts
  and backup port publication stability
- fixed read-only SHOW SETTINGS from the already-running API, using
  credentials inside its existing private Neo4j environment, never
  returning raw usernames or passwords
- fixed docker exec neo4j cat /proc/net/tcp and /proc/net/tcp6,
  converting address values only into loopback/wildcard/nonloopback classes
- stat/statvfs and bounded filename-only inventory of backup artifacts;
  it never opens an archive or reads application graph data

The tool emits only bounded numeric/boolean statistics and fixed risk
categories. All untrusted Docker/query errors are suppressed, rather
than exposing possible secret-bearing stderr. It never runs an online
backup, opens backup TCP connections, changes firewall rules, restarts
services, writes graph records, rotates credentials, publishes new ports
or dispatches Tasks.

Actual x1-370 image-pinned run, 24-hour RPO:
- native_backup_enabled=true
- native_backup_listen_setting_loopback=true
- actual_backup_kernel_listeners=1, all loopback
- host_published_backup_bindings=0
- fresh_archive_count_in_data_mount=0
- historical_dump_count=1, backup_chain_file_count=0
- data_mount_free_gib=175.3 (approximate at inspection time)
- backup_run_authorized=false
- production_cutover_authorized=false
- fresh_backup_restorability_verified=false
- production_decision=HOLD, exit 3, even with a healthy listener

## Next owner-approved P0 recovery work (NOT executed)

1. Inventory every database and transaction high-water mark to protect,
   approved backup credential/protocol, full vs differential strategy,
   expected compressed size, and tolerable read pressure on live Neo4j.
2. Prove bounded connectivity from a restricted backup client without
   changing the loopback-only native listener, and verify that client
   cannot issue arbitrary graph or Task writes.
3. Obtain an approved protected destination large enough for a fresh
   archive AND separate disposable restore; verify retention, encryption,
   independent off-host custody, signed receipt/anti-rollback, explicit
   RPO and recovery owner.
4. Take a fresh backup only after operational authorization, assess
   full/incremental chain and transaction continuity, and restore into
   an isolated nonproduction database with CPU/memory/disk limits.
5. Complete application-level Task/lease/source-writer integrity checks
   and a reversible private-network cutover canary.
6. Independently resolve remaining P0 barriers: three broad admin graph
   principals, 49 unresolved Task query/callback sites, secondary socat
   forwarder, wildcard host graph publishing, host/OPNsense firewall
   ingress, machine Task 401 and expired signed work admission.

No live backup was taken. No live credentials, roles, records, ports,
containers, service configuration, Task dispatch or approvals changed.
