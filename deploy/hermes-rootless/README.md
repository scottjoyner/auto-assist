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


## x1 NAS5 canonical-route reconciliation (2026-10-21)

Fleet Hardware docs/TOPOLOGY.md records x1-370's NAS endpoint as
/nas backed by the Beelink SMB share //192.168.1.202/fileserver.
Read-only findmnt inspection confirmed that /nas currently has a CIFS
mount at exactly this source, layered above systemd autofs. The unit
nas.mount is active; /nas has a different filesystem device from
host root. This confirms a ROUTE MATCH to the documented inventory,
not independent authentication of the physical Beelink Btrfs pool.

By contrast, /media/scott/NAS5 is currently an ordinary directory
on the host root filesystem (not a mountpoint). A metadata-only
Docker inspection confirms that the still-running production Hermes
container continues to bind that legacy path, RW. The live Hermes
bind therefore does not reach the verified /nas CIFS route.
NO symlink, bind mount, service update or automatic fallback to /nas
was performed. Existing production state is deliberately unchanged.

verify_nas5_route.py is a read-only bounded reconciliation witness:
- Default invocation does not contact Docker or inspect mounts.
- Explicit --physical-readonly only on nonroot x1.
- Calls findmnt on exact /nas and Docker inspect on the existing
  Hermes container; inspects only Mounts, never Config.Env.
- Requires a single expected /nas CIFS route, distinct from host
  root device, rejects an autofs-only placeholder or different
 /duplicate SMB export; compares the running legacy bind.
- Emits only booleans and violation categories, not hostnames,
  share addresses, mount source paths, credentials or content.
- Every outcome says decision=HOLD with no release authority.

Physical x1 verification:
canonical_nas_cifs_matches_fleet_inventory=true,
legacy_nas5_exact_mountpoint=false,
legacy_nas5_uses_host_root_filesystem=true,
running_hermes_has_legacy_nas5_bind=true,
legacy_bind_misses_canonical_cifs=true,
remote_share_custody_independently_attested=false,
production_knowledge_mount_authorized=false, decision=HOLD.
The source-level test suite increased to **68/68 PASS**.

This narrows the topology problem to a LEGACY ROUTE MISMATCH,
not evidence of all NAS connectivity being down. Do not redirect
Hermes to /nas wholesale: it could expose the entire NAS to
agent writes, knowledge loops and cross-host traversal.
The remaining safe next step is an owner-reviewed curated,
limited-scope, separately versioned knowledge export (RO),
with canonical identity, independent custody, revocation and
protected Watchdog evidence denial, followed by a cutover
and rollback test. P0 #414 and fleet-hardware #5 remain OPEN.


## Bounded, manifest-pinned SYNTHETIC curated knowledge (2026-10-21)

New opt-in verify_curated_knowledge.py runs with default HOLD and
no filesystem scanning, Docker access, model invocation, mounts,
provider calls or NAS reads. It accepts only a tiny disposable
synthetic fixture under a private 0700 x1 test root named
og-hermes-knowledge-* in /home/scott/.cache or /tmp.
A separate mode-0600 synthetic manifest must have the fixture's
unique basename plus .manifest.json and be a sibling of the fixture
root. No real NAS directory, legacy /media/scott/NAS5 path, SSH/agent
repository, or protected Watchdog evidence can be admitted.

Manifest format is schema=1, kind=synthetic-hermes-curated-v1,
generation=synthetic-<id>, and an exact allowlist of file entries
(path, size, sha256). It accepts at most 16 files, max 512 KiB per
file, 2 MiB total, a maximum of six directories and no unlisted
files or directories. Names are restricted; forbidden tokens
include watchdog, dashcam, archive, credential, secret, SSH,
token, password, hidden files and traversal. Files must be
regular, single-link, owned by the current user, on the same
device as the private scratch root, and free of symlink escapes.
The checker opens only eligible synthetic fixture files with
O_NOFOLLOW, compares file metadata before/after and verifies
complete SHA-256 hashes against the exact manifest.

Physical Fleet Commander qualification created only two tiny
synthetic files under a disposable scratch root. Manifest check:
synthetic_curated_fixture_verified=true, files_verified=2,
logical_bytes_verified=50, violations=[], decision=HOLD.
A deliberate synthetic file change was correctly denied;
default no-source invocation denied without scanning. Afterward
the fixture and unique manifest were removed. The original
Hermes adapter remained running unchanged; no real NAS contents,
indices, protected data or credentials were touched.

The dedicated rootless test suite increased to 88/88 PASS;
new negative cases cover unsafe source roots, hidden files,
symlinks, hardlinks, duplicate/extra files/directories, forged
manifest fields, path traversal, hash drift and size caps.

This is **only a source-structure and integrity rehearsal**:
the manifest has NO independent signature, cannot authenticate
an upstream storage owner and has no monotonic generation
replay protection. Same-user mutations after the check remain
possible, and no actual stable production knowledge corpus has
been selected. Result ALWAYS says decision=HOLD,
independently_signed_corpus_authority=false and
production_knowledge_mount_authorized=false.

A real release still requires separate independent Beelink/source
custody, explicit content-level protected-evidence exclusion,
a scope-limited *immutable* knowledge snapshot and signatures
held outside the worker's administrative domain, verified
anti-rollback, and the authenticated task/revocation and
restart/rollback gates. Do not point this checker at /nas or
substitute its synthetic PASS for release authority.

## Synthetic two-role Ed25519 signed-curated manifest contract (2026-10-21)

New `verify_signed_curated_knowledge.py` composes the already
bounded synthetic curated-knowledge verifier with an in-memory,
two-role Ed25519 signed envelope. No production knowledge is used.
Default command has **no external inputs** and returns HOLD without
filesystem, Docker, network, NAS, or signing-key operations.

A synthetically valid envelope contains exactly schema 1, explicit
non-production domain `assistx.hermes.synthetic-curated-signature.v1`,
a positive ordered integer sequence, previous_sha256 checkpoint head,
the existing tiny `synthetic-hermes-curated-v1` manifest, and exactly
two signature strings for producer and reviewer. All signed payload
fields are canonical JSON with fixed domain separation. A reviewer
cannot sign as the producer or vice versa, and using the same public
key for both roles fails closed. Signature verification is completed
BEFORE any synthetic corpus read; only then does the source verifier
check the strictly allowlisted local scratch data.

An explicitly supplied synthetic checkpoint must contain only
schema, sequence and head_sha256. It requires the candidate sequence
to advance by exactly one and previous_sha256 to match the supplied
head. It rejects stale, skipped or forked sequences. The resulting
synthetic head digest can be used in a next-iteration offline test;
the verifier **never writes or persists a checkpoint**, never chooses
trusted keys, and never grants release authorization.

x1-370 ran the complete dedicated suite: **104/104 PASS**, including
16 new signature-domain, forged-signature, swapped-role, same-key,
tampering-before-read, stale/replay, wrong checkpoint, changed-file,
wrong-root, extra-field and two-generation chain tests. Only in-memory
ephemeral Ed25519 test keys and ephemeral `/tmp` synthetic fixture
files were used. No private signing keys are saved or exposed.

**Key limitation:** caller-injected public keys and checkpoint provide
no independent identity, custody, anti-rollback or protection from
a malicious worker with control of both the fixture and claimed
checkpoint. A two-role cryptographic proof with worker-controlled
keys is not independently authorized production evidence. Even a
successful synthetic verification always says `decision=HOLD`,
`independent_key_custody_verified=false`,
`rollback_resistant_external_checkpoint=false`,
`production_knowledge_mount_authorized=false` and
`production_deployment_authorized=false`.

Before real knowledge admission, separate authoritative trust roots,
real independently signed manifests, durable offhost monotonic
checkpoints, actual protected-data exclusion, immutable curated
snapshots, source-owner effects authorization and rollback must be
physically demonstrated. P0 #414 stays OPEN, PR #424 DRAFT, live
Hermes still unsafe with 16 mounts (9 RW); do not deploy.
