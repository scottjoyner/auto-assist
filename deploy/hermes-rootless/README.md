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


## Read-only Docker runtime mount policy (October 21, 2026)

The new verify_runtime_mounts.py is a fail-closed, non-deploying
Docker metadata verifier. Its default invocation does NOT contact
Docker or modify files. For x1-370 only, --audit-live-readonly
performs one Docker inspect of the existing Hermes container.
It emits only policy booleans, violation codes, mount counts and HOLD.
It NEVER prints host source paths, environment values or credentials.

This code checks only the runtime **configuration**, not authentic
ownership of a Docker daemon or physical protected evidence data.
The synthetic policy requires a pinned immutable image object ID,
UID/GID 1000, the hardened image entrypoint, network mode none,
readonly rootfs, all Linux caps dropped, NNP, no host pid/ipc/devices,
no published ports, capped resources and **exactly two** approved
canonical synthetic bind source paths: knowledge RO at
/research/knowledge and private workspace RW at /workspace.
Symlink/alias source paths, broad source mounts, extra writable
mounts, unexpected destinations or privileges fail closed.
An inherited image EXPOSE declaration is metadata, not an active
published port; actual port bindings and network access are forbidden.

A physical read-only inspection on x1-370 rejected the ORIGINAL
running adapter (16 binds, nine RW, root user, writable rootfs and
broader networking). The witness intentionally lacks any approved
synthetic sources for that production container. It returned HOLD
and did not print production host source paths.

For a separate positive physical qualification, a reviewed
candidate overlay was built offline from the exact local running
Hermes base, and a disposable Docker container was CREATED (not
started) with UID 1000, no network, no caps, NNP and exactly two
temporary synthetic bind sources. The verifier consumed its real
docker inspect metadata with those sources and its exact image ID.
Result: synthetic_runtime_policy_pass=true, mount_count_observed=2,
writable_mount_count=1, violations=[], decision=HOLD. The scratch
container, temporary image tags and synthetic fixture paths were
removed. Previous rootless Compose tests separately proved the
container can actually start and run a bounded synthetic workload.

The new source-only test file tests/test_hermes_rootless_runtime_mounts.py
contains safety negatives for host networking, root user, privileged
mode, cap addition, devices, open ports, resource limits, mount
substitution, source symlinks, extra mounts and policy-output
redaction. Total staged dedicated tests: **46/46 PASS**.

This check is NOT an authenticated protection against Docker-admin
access to host storage; Docker-root authority can change mounts.
It does not establish signed image custody, production protected
evidence denial, true source-owner admission/revocation,
normal unbounded agent runtime safety or rollback. The only
permitted result for production remains HOLD under P0 #414.


## Strict synthetic source admission and NAS5 mountpoint gate (2026-10-21)

The read-only runtime mount witness now has an explicit SYNTHETIC source
boundary. A caller cannot turn any canonical host directory into an
approved bind merely by passing it as readonly_source/private_source.
Only a private 0700 ephemeral scratch root under /tmp or
/home/scott/.cache is eligible, with the controlled
hermes-runtime-test- or og-hermes-audit- root prefix. It must contain
two real non-symlink directories named knowledge and workspace, with
the same parent. The scratch root and children must be owned by the
current user, canonical, non-mountpoints, and outside protected
/media/scott, repository, credential and operating-system paths.
This is a synthetic-only policy; it deliberately CANNOT approve a
real curated production knowledge directory.

Fleet Commander may use an isolated HOME not equal to /home/scott.
The authorized x1 scratch parent is pinned explicitly, never derived
from that process's HOME variable.

Additional metadata-only witness verify_storage_mountpoints.py has
NO Docker operations and never opens directory contents. Its default
mode does no physical filesystem operations. Explicit nonroot x1
--physical-metadata-only compares exact NAS5 and SSD mountpoints and
their device IDs to the host root filesystem. Its positive case is
still HOLD (mount topology alone never proves custody).

Physical x1 observation: /media/scott/NAS5 existed as a directory
but was NOT an exact mountpoint and had the SAME device as the root
filesystem. /media/scott/SSD_4TB was an exact mountpoint on a distinct
device. Result: metadata_topology_gate_pass=false; failures
nas5_not_exact_mountpoint and nas5_on_host_root_filesystem.
No media contents were read and no mounts were changed. This is a
release HOLD: do not infer the NAS5 data is missing or start new
writes to that path; verify the expected NAS mount with its owner.

The strengthened runtime policy returned synthetic_runtime_policy_pass
true on a freshly created disposable Docker container with two
approved synthetic mounts, UID 1000, no network, RO rootfs, dropped
caps, no-new-privileges and resource caps. It returned HOLD and zero
release authority. The live production container was again REJECTED
(16 mounts, 9 RW). No original running container was modified.

Total local suite: 57/57 PASS, including synthetic source substitution,
weak scratch permissions, symlink/traversal and wrong mountpoint
negative controls. Temporary candidate image, container and scratch
were cleaned. This work does not authorize protected-evidence access,
production knowledge ingestion, model dispatch or PR merge.
