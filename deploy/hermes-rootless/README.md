# Hermes rootless entrypoint — research candidate only

**HOLD**: this directory is an opt-in overlay proposal, **not a production
deployment**. The current production Hermes container still has unsafe writable
SSD/knowledge aliases overlapping protected evidence. Do not reconfigure it,
merge this candidate, or cut over traffic without independent review.

The existing main `/entrypoint.sh` assumes root and can serialize environment
variables. This directory adds a **separate** nonroot-only candidate, leaving
the production default entrypoint unchanged.

## Source and build

Use only a reviewed local base image whose exact source-image provenance is
verified. Pin the base by immutable object digest or authenticated registry
digest; never use a floating `latest` image. Supply it explicitly as
`HERMES_BASE_IMAGE`; the Dockerfile intentionally has no default.

For reproducibility testing, set `SOURCE_DATE_EPOCH=1704067200` in
the environment **and** as `--build-arg SOURCE_DATE_EPOCH=1704067200`.
Use `--network=none --pull=false --no-cache --provenance=false` only
for isolated local test builds. The no-provenance flag is **not**
release provenance; a real release still requires signed independently
verified evidence.

This overlay changes only `/entrypoint.sh`. A pair of matching local
build hashes is not proof that the inherited application dependencies
or source were independently rebuilt or audited.

## Candidate execution boundaries

Nonroot UID and GID, read-only root filesystem, `network=none`,
capabilities dropped, `no-new-privileges`, limited CPU/memory/PIDs,
no production mounts, synthetic knowledge mounted RO and a synthetic
Hermes-owned state workspace mounted RW. The entrypoint must not
write any bearer token to `.env` or assume `/root`.

The candidate cannot use normal agent APIs while `network=none`;
a fully isolated mock service and task-lifecycle proof are still pending.
The synthetic fixture must never be included in the production image.
The original worker remains running under P0 #414 until owner-bound
execution, revocation, evidence isolation, and rollback are reviewed.

## Offline tests

Run `python3 -m unittest discover -s tests -p 'test_hermes_rootless_candidate.py'`
from the repository root. Tests use only temporary directories and synthetic
environment values; Docker and production services are not touched.
This is source-level validation, not an image-runtime acceptance test.

## Hardened writable-path policy

The proposed nonroot startup refuses UID 0 and any ENV_SEED setting.
HOME must already exist, be writable, absolute and canonical; no
symlink aliases or dot-dot components are accepted. HERMES_HOME and
HERMES_CONFIG_HOME must be exactly HOME/hermes-home and HOME/.hermes.
Existing writable directories must be real directories, not symlinks,
including the sessions/logs/cache/harvest/memories/profiles/tmp paths.
The primary config.yaml may not be a symlink, and the separate
configuration symlink must point to the exact private primary config.
Unknown existing config files are rejected rather than replaced.

These checks are defense in depth, **not a substitute for a restricted
mount namespace and private single-owner workspace**. A shell script
cannot independently prevent filesystem races by other actors sharing
its writable directory. Never attach legacy wide knowledge or
untrusted RW directories. Static path checks alone do not authorize
a release or independent evidence custody.

The additional isolated negative suite is
`python3 -m unittest discover -s tests -p 'test_hermes_rootless_*.py'`;
it includes dot-dot, symlink, alternate writable-root, corrupt
configuration and idempotency probes.
