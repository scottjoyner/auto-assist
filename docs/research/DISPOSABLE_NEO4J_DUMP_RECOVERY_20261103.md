# Verified historical archive restore — not a current cutover gate

Date: November 3, 2026. Source: Fleet Commander on x1-370.
Decision: research PASS for historical restore, production HOLD / NO-GO.

## Real observed archive and runtime evidence

- One regular archived Neo4j dump was found under the running Neo4j
  data bind mount (no data was changed).
- Archive size: 868,045,968 bytes (about 0.81 GiB).
- Archive age at run time: approximately 70.36 days.
- The exact database archive is named for the assistx database, NOT
  neo4j.dump. Blindly invoking the info command with the database
  name neo4j returns exit 0 and EMPTY output; that is NOT evidence.
- Correct Neo4j 5.26.26 read-only archive info returned:
  Database: assistx; Format: Neo4j ZSTD Dump.; Files: 335;
  Bytes: 7,749,137,182 (about 7.22 GiB unpacked).
- SHOW DATABASES against live Neo4j confirmed assistx is online.
  This does NOT tie the dump to any current transaction high-water
  or prove a recovery-point objective.

## Reproducible isolated restore

scripts/disposable_neo4j_dump_restore.py provides an EXPLICIT
research-only and resource-bounded disposable restore. Example:

python3 scripts/disposable_neo4j_dump_restore.py \
  --expected-image sha256:FULL_RUNNING_NEO4J_IMAGE_ID \
  --explicit-disposable-restore \
  --allow-historical --max-age-hours 24 --json

Before any Docker call, the tool requires a valid immutable image
pin, explicit disposable restore flag, bounded RPO, one matching
assistx.dump regular file, a trusted data mount, and sufficient
scratch free space. An old dump is refused unless the operator
explicitly requests a historical research experiment; such an
experiment never becomes production-safe.

The tool reads the archive via a Docker read-only bind mount, but
mounts an EMPTY unique scratch-only data directory for the restored
Neo4j store and scratch-only logs. The container has:
- no network, no published ports, no Docker socket and no extra devices
- read-only container root, all capabilities dropped, no-new-privileges
- 5 GiB memory, 2 CPUs and 128 PID limit
- one temporary /tmp filesystem with execute permission, needed to
  map Neo4j's Java native archive decompressor library
- a fixed local-only hostname mapping for Java without DNS access
- no service daemon and no production config/credential mounts

Each CLI phase uses the pinned Neo4j image: archive load --info,
database load into scratch, and offline full database check.
The consistency check includes graph, counts, indexes and property
ownership; writes are restricted to scratch paths. The SHA-256
digest and inode/size/mtime of the SOURCE archive are rechecked
afterwards, and the live container identity is re-inspected.
The script cleans disposable containers and scratch on completion.

The actual x1-370 run using the currently pinned Neo4j image verified:
- Restored 329 physical database files (about 7.22 GiB).
- Neo4j archive metadata and database offline store-info PASS.
- Actual offline full consistency exit 0 after roughly 26 seconds.
- Source archive digest stable across the test.
- Research script run succeeded with explicit result PASS but CLI
  exited 3 (HOLD), since archive age exceeds 24-hour RPO.
- No disposable container or scratch directory remains.
- Production API and Neo4j images unchanged and both services running.

This demonstrates HISTORICAL restorability and internal archive
consistency, not recoverability of the current live Task database.
It does not exercise client reconnect, graph restore at production
scale, backup encryption, independent custody, anti-rollback,
immutable off-host retention or application-specific integrity.

## Existing backup-chain tool versus dump format

The repository's existing Neo4jBackupVerifier inspects Neo4j
Enterprise online backup chains and transaction continuity.
It should continue to validate fresh .backup chains. The artifact
tested here is a .dump and requires the separate archive-info,
disposable load and offline database check. Treating a dump as
a current Enterprise incremental backup is a category error.

## Remaining hard blockers

- The only inspected dump is more than 70 days old; fresh independent
  backup and restored recent transaction coverage remain unverified.
- Running Neo4j still has all-interface host Bolt/HTTP publishing,
  three Docker networks and a secondary socat listener whose
  loopback destination has returned Connection refused.
- API/worker/Hermes graph principals remain admin-capable, with
  broad native WRITE privileges. Source Task writer census HOLD
  with 49 unresolved query/callback sites.
- Effective host NAT/firewall and upstream OPNsense/Tailnet controls,
  signed independent custody, machine-auth Task 401 and current
  work admission remain unresolved (zero approved slots).
- No live network migration, credential rotation, Task mutation,
  model activation, signed approval, restore, or dispatch is allowed.

Production NO-GO. Research-only archived data was isolated,
tested without network access and deleted from scratch after use.
