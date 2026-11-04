# Actual NAS full backups and native metadata parser correction

**November 3, 2026, Fleet Commander on x1-370. Research PASS; production NO-GO.**

## Important correction to previous scoped RPO findings

Previous backup searches inspected only the running Neo4j container's
data mount. They correctly found a roughly 70-day-old assistx dump
there and no fresh .backup chains IN THAT LIMITED DIRECTORY. They did
not inspect the already mounted, distinct NAS backup destination.

A read-only bounded scan of existing /nas/neo4j-online-backups found
36 native Neo4j Enterprise .backup files: 12 each for assistx, neo4j,
and system, dated October 23 through November 3, 2026. All latest
file timestamps are within 24 hours of the Nov 3 observation.
The active NAS CIFS mount has approximately 35 TB free.

An immutable-image-pinned, network-disabled disposable Neo4j 5.x
container mounted the NAS backup destination **read-only** and ran
neo4j-admin database backup --inspect-path. Its 36 native metadata
rows confirm every listed artifact is FULL=true and COMPRESSED=true,
the database UUID remains stable per database, and transaction
high-water marks increase throughout each database's 12 backups.

At the observation time, native metadata inspection found:
- assistx: 12 full backups, newest ~17.7 hours old, last TX 15,112,223
- neo4j: 12 full backups, newest ~17.8 hours old, last TX 22,824,280
- system: 12 full backups, newest ~17.7 hours old, last TX 178

These metadata results verify backup identities, 24h header age and
reported transaction coverage only. They are NOT an immutable remote
custody receipt, live transaction high-water comparison, or complete
restore proof. No private NAS backup file enters GitHub Actions.

The repository's active NAS backup directory is writable via CIFS;
its separate rootlock quarantine directory and operator README
document an Oct 23 permissions incident and subsequent recovery.
Write-accessible files plus a stable database ID do not prove
independent custody, immutable retention or anti-rollback.

## New defect reproduced and patched

The existing Neo4jBackupVerifier._parse_table had two security-relevant
bugs exposed by REAL native 5.x table output:

1. It searched for the first cell containing uppercase T to find
   the timestamp. The FILE column is a URI whose filename includes
   a timestamp, e.g. assistx-2026-11-03T08-20-31.backup, so the
   parser selected the FILE field, failed ISO time parsing, produced
   0 (=1970) and falsely rejected recent backups.
2. It identified FULL by asking whether any of columns TIME, FULL or
   COMPRESSED equals true. A compressed differential with FULL=false,
   COMPRESSED=true was falsely accepted as a full backup.

The JSON parser also used Python bool("false"), which yields True, to
derive FULL and recovered flags. That could falsely attest that a
differential is a full backup or that an unrecovered backup is recovered.

The patch explicitly parses all eight native table columns: exact
DATABASE match, UUID-shaped database identity, TIME from column 4,
strict FULL and COMPRESSED booleans from their respective columns,
nonnegative ordered LOWEST/HIGHEST TX. It fails closed when a
target database's native row is malformed. JSON FULL and recovered
flags now accept only actual booleans or case-normalized textual
true/false, not truthy strings, numbers or unrecognized values.

## Real restoration evidence and remaining release boundaries

A current dated assistx .backup file (~0.80 GiB) was restored
successfully into a NEW scratch database under /tmp (a separate
filesystem from production graph data). The NAS backup was mounted
READ-ONLY, and Neo4j temporary extraction was pointed to a second
scratch-only write mount using --temp-path. The disposable container
had --network none, read-only image root, capability drop,
no-new-privileges, 5 GiB memory, 2 CPU and 128 PID limits.

This restored 336 database files (~8.64 GiB); Neo4j's full offline
graph/index/count/property-ownership consistency check PASSED.
Source archive SHA-256 was stable before and after; the production
Neo4j container remained unchanged. The scratch database and
research containers were deleted after testing.

A separate full-scale restore for the much larger (~70 GiB) neo4j
database must have its own verified result before asserting both
databases are restored. The system backup should be tested separately.
Never infer restoration for one DB from another's successful drill.

## Release gate matrix and safe follow-up

- NAS file existence and native header RPO for 3 databases: PASS.
- Native parser strict timestamp/full/recovered contract: PASS in
  local source tests; hosted exact-head results checked separately.
- Current assistx archive isolated restore and offline consistency: PASS.
- Current neo4j and system archive restores: separately pending until
  validated.
- Independent immutable custody/off-host anti-rollback: HOLD.
- Restricted backup identity and live transaction high-water capture:
  HOLD.
- All-native graph writer privilege separation, 49 unresolved Task
  writers, effective host/OPNsense firewall, secondary socat forwarder,
  machine Task GET 401, expired signed work projection: HOLD.

No live database, backup service, credentials, roles, routes, firewall,
Task, model, graph record or approval was modified. Production NO-GO.
