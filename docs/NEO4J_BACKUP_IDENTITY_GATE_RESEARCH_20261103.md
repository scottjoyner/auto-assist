# Backup identity verification — fail-closed research gate

DRAFT / SOURCE-ONLY / NO PRODUCTION MUTATION OR RESTORE.

## Physical evidence — November 3, 2026

The running AssistX API uses the live Neo4j database named assistx.
Native read-only SHOW DATABASES confirms that both assistx and the separate
default neo4j database are online and have DIFFERENT native database IDs.

The Nov 1–3 NAS backup files are under:
  /nas/backups/neo4j/deathstar/bodycam-2026-11-0N.../
Each is about 31.8 GB, with filenames beginning neo4j- and no companion
checksum or provenance files in those three immediate directories. These
names and file sizes do not prove source database, integrity or restore.
No bytes from these large archives were read for this patch.

## Source defect

The existing operator backup CLI silently defaults its --database value
to neo4j, despite AssistX using the distinct database assistx. The parser
also substitutes the requested DB name for JSON metadata missing any
database name. The legacy table parser could match a requested database
name appearing only in a filename while the DATABASE column specified
a different source DB.

## Narrow correction

- Require explicit --database in the operator CLI.
- Reject JSON rows without an explicit matching DB name.
- Require the actual DATABASE column in legacy tabular backup inspection.
- Require a nonempty, unique source database ID across a backup chain.
- Support optional independently attested live database ID pinning and
  reject any archive that does not match.
- Never claim live DB identity was pinned when no UUID was supplied.
- Return database_restore_certified=false even for a parsed valid chain.

The existing backup verification default functionality remains usable for
any explicitly selected DB. No live archive is opened or hashed.

## Synthetic validation and release gate

The source tests use mock Neo4j-admin JSON and table responses only.
They prove matching name/UUID positive, missing/wrong name negative,
table filename-spoof negative, missing/mixed/mismatched UUID negative,
CLI missing --database rejection, and no production filesystem use.
Dedicated GitHub workflow checks exact head with read-only permissions.

A real release must separately establish source database identity,
archive contents/checksum, independent custody, a true isolated Neo4j
restore, graph identity, transaction-consistency checks, and rollback.
Swap-reserve and Task admission remain held. Production NO-GO.
