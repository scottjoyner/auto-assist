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


## Synthetic Compose confinement — physical milestone (2026-10-21)

The separate compose.synthetic.yml is NOT a production Compose override.
It has exactly one opt-in synthetic-canary service with network=none,
non-root UID 1000, readonly image root, all capabilities dropped,
no-new-privileges, resource limits and pull_policy=never.
There are exactly two required synthetic binds: readonly knowledge
and the sole writable private workspace. Docker may not automatically
create either bind source.

The synthetic_smoke.sh command intentionally replaces the real agent
loop with a bounded storage and startup probe. It uses NO real task,
provider, signed claim, network, production file or secret.

On x1-370 the Docker Compose JSON render passed. A new disposable
local entrypoint-overlay image from the exact live image object
ran this explicit service with docker compose run --rm --no-deps.
It printed ROOTLESS_COMPOSE_STORAGE_ISOLATION_PASS. It checked denied
writes to synthetic readonly knowledge and the readonly rootfs,
private workspace write success, hidden protected host paths,
NoNewPrivs=1, zero effective Linux capabilities, and initialization
without a credential .env. Synthetic evidence marker stayed intact.
Docker-only scratch resources were removed afterward.

Thirty of thirty local rootless tests passed, including mutation
negatives for host networks, extra mounts, privileges, ports and
unexpected actual agent execution. This demonstrates the staged
synthetic container boundary, NOT a safe production cutover.

Still HOLD: real strict-executor entrypoint + isolated mock claim
service, signed image/source provenance, true owner-bound execution
and revocation, scoped index provenance, independent evidence custody
and verified rollback. Do not merge or activate production resources.


## Physical rootless strict-executor synthetic lifecycle (2026-10-21)

A second opt-in physical qualification on x1-370 combined the
hardened normal image ENTRYPOINT with the actual Python
assistx.strict_executor_main.main() function. The default unbounded
poll loop was replaced ONLY in a synthetic test fixture with one
call to its existing run_loop(once=True). Requests, subprocess creation
and the engine result were mocked. No network interface was enabled.
The shipped synthetic Compose default remains the benign shell probe.

The fixture is tests/fixtures/hermes_rootless_synthetic_lifecycle.py.
It is NOT built into any image or executed by default. For a physical
test, an operator must put the script into an entirely synthetic
disposable workspace and override the command with
python3 -B /workspace/synthetic_lifecycle.py, using only synthetic
strict-executor token strings. Never use real provider or API secrets.

The physically executed rootless disposable Compose instance used the
exact existing Hermes image as base and the hardened entrypoint.
Confinement: network=none, rootfs read-only, zero Linux capabilities,
no-new-privileges, RO synthetic knowledge, private RW test workspace,
nonroot UID and tightly bounded CPU/memory/PIDs.

The actual strict executor Python code executed mock poll -> claim ->
claim token -> heartbeat -> context -> heartbeat -> fake engine result ->
memory -> DONE completion. A second claim lost its lease; its attempted
completion was denied, and a mismatched claim-token identity was rejected.
Private synthetic model ENV.md and evaluation registry files appeared
only under the scratch workspace. Console proof:

    ROOTLESS_HERMES_MAIN_SYNTHETIC_LIFECYCLE_PASS

Five additional source-level contracts ensure the fixture requires a
bounded one-pass main() loop, synthetic HTTP/process mocks, known token
strings and lease-loss denial. Total local suite: 35/35 PASS.

This is not live API evidence or real model/CLI execution. Actual
protected evidence write-denial, independently custodied claims and
revocation, signed image-source identity, normal restart/rollback
and scoped memory index are NOT VERIFIED. The running adapter
retains its original 16 mounts, including nine RW and two aliases to
protected watchdog evidence. Keep PR in DRAFT and P0 #414 OPEN/HOLD.
