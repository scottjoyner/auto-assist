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

## Second exact-head parser defect, corrected after hosted feedback

The same verifier had another fail-open condition: Python bool("false")
is true, so JSON inspection flags encoded as "full":"false" and
"recovered":"false" were previously treated as approved full/recovered
backup evidence. A synthetic inspect response demonstrated that
require_recovered=True incorrectly passed with both flags false.
The legacy 5.x table fallback also determined full using any("true")
across the TIME/FULL/COMPRESSED columns, allowing a compressed
differential (FULL=false, COMPRESSED=true) to be misclassified as full.
Its database ID search could accept a UUID found in a filename instead
of the actual DATABASE ID column.

The source now requires strict true/false inspection semantics (actual
JSON booleans or literal true/false strings), fails closed for invalid or
absent required flags, reads the actual table FULL column, and extracts
the database ID from its dedicated table column. Synthetic negative
tests cover false strings, false booleans, malformed values, compression
confusion and a filename-embedded UUID. 45 focused synthetic tests
passed locally after this amendment, plus Ruff F/I and compilation;
the new exact-head hosted workflows must be checked independently.

No physical archive has been inspected, hashed or restored by any of
these synthetic tests. Production recovery remains NO-GO until the
matching live database identity, independently verified source archive,
and disposable restore are established.
