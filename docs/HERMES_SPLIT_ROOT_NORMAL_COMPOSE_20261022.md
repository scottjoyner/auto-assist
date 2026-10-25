# Hermes split-root: exact-source normal-entrypoint Compose acceptance

**Date:** October 22, 2026
**Repo/PR:** `scottjoyner/auto-assist` draft PR #425 (HOLD)
**System:** x1-370 research. No production cutover or provider egress.

## Executed qualification

The source identity in this candidate comes from commit
`412affd187acffe2147f03088e1e126472f4667e` (original patch in PR #425).
The dedicated runner validates ancestry **and the exact blob digests** of
`knowledge_roots.py`, `swarm_memory.py`, and
`agents/hermes_agent_adapter.py` before building. It also verifies the
SHA-256 of the previously reviewed rootless `/entrypoint.sh` fixture,
`81add7ecd43ef1b3fccc5d8097b4f51ed196cfb61d5ccd3cd243e51a552ab6f5`.

A random, disposable Docker Compose project was created using **only** the
synthetic-canary service declared in
`tests/hermes_split_root_normal.compose.yaml`. The image is an offline,
no-pull overlay of the exact image still backing production Hermes:
`sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307`.
Only the three exact reviewed patched Python files and the reviewed non-root
entrypoint are overlaid in the temporary build context.

The runner validates the effective `docker compose config --format json`
before creating anything. It then verifies Docker container image ID,
service/project ownership labels, normal `/entrypoint.sh`, real
`assistx.strict_executor_main` command, non-root UID 1000, `network=none`,
read-only rootfs, dropped Linux capabilities, no-new-privileges and
CPU/memory/process limits. There are precisely **two** mounts: a synthetic
read-only input corpus and a synthetic writable scratch. It does not attach
any production SSD, NAS5, Git repo, credential or external port.

The test-only `sitecustomize.py` fixture runs the *actual image-baked*
patched adapter through the normal strict executor entrypoint. It first calls
the adapter's real read-only context and self-task/evaluation functions using
synthetic data with a stubbed model response. It then runs one normal
no-task polling cycle with `requests.request` intercepted. No real model,
provider, agent task or production API is contacted. In a second full
container start, the adapter confirms the persisted evaluation registry,
scratch artifact, source integrity and no write to the read-only corpus.

**Result:** 9/9 physical checks pass (repeated); focused tests 61/61 pass,
including 25 new offline Compose contract tests. All disposable images,
containers and scratch files are removed; the original production Hermes
remains running on the original image.

## Exact commands

No-Docker / no-side-effects report:

```bash
python3 tests/hermes_split_root_normal_compose_canary.py --json
```

Explicit, temporary x1-370 physical canary:

```bash
python3 tests/hermes_split_root_normal_compose_canary.py --physical-disposable-compose --json
PYTHONPATH=src python3 -m pytest -q \
  tests/test_hermes_split_root_normal_compose_canary.py \
  tests/test_hermes_split_knowledge_roots.py \
  tests/test_hermes_delegated.py \
  tests/test_hermes_memory_provider.py \
  tests/test_hermes_external_config.py
```

These commands are not production deployment instructions.

## Remaining release gates

- **P0 issue #414:** production still has 16 mounts / 9 RW, including
  overlapping writable protected SSD ancestors and broad NAS/repository
  surfaces. None were remediated by this qualification.
- Reviewer-independent, signed source/image provenance and a reproducible
  authorized production build are not established. The local hash/ID check
  proves identity relative to a PR, not independently controlled custody.
- This is synthetic model output and synthetic polling, **not** live
  authorized ingestion, real task receipts or production model execution.
- Production owner grants, durable independent revocation/fencing,
  concurrency, independent review, and tested rollback are unverified.
- Hosted CI for the new commit must be confirmed separately before any
  merge recommendation. The earlier PR #425 CI-green result applies only
  to that previous head.

**Decision: HOLD.** Do not merge, restart Hermes, or replace mounts from a
synthetic qualification alone.

## Strict kernel mount admission follow-up

A newer opt-in guard now verifies exact RO corpus and distinct RW scratch mountpoints from kernel mountinfo at adapter startup, before any synthetic self-task execution. The new proof and limitations are in [HERMES_KERNEL_MOUNT_ADMISSION_HOLD_20261022.md](HERMES_KERNEL_MOUNT_ADMISSION_HOLD_20261022.md). This does not remove independent source custody or grant a production release.

## October 24: explicit refusal to auto-create missing host bind sources

The research-only restricted Compose fixture now sets `bind.create_host_path: false` on **both** existing `volumes` declarations: the read-only `/research/knowledge` input and the independently writable `/workspace` output. The existing `restricted_render()` verifier requires these exact false values and refuses omitted, `true`, or unexpected bind options. This counters a Docker Compose default that may create absent host directories when binds are declared without explicit no-create semantics. Missing paths should fail rather than silently becoming new local directories.

On x1-370, installed Docker Compose **v5.6.0** `config --format json` (render-only, no container creation) retained both `create_host_path: false` flags. The exact rendered synthetic service passed `restricted_render()`, and neither deliberately absent dry-run source path was created. The surrounding offline negative tests also reject removal or reversal of either guard. No image rebuild, Docker container start, mount operation, production corpus access, credential read, task execution or provider call was performed in this validation.

**Important limitation:** `create_host_path: false` only prevents *automatic creation* of an absent host bind source. It **does not detect an already existing local directory** below a failed/unmounted `/nas` share. The separate metadata-only physical CIFS + systemd automount witness must prove the expected remote source immediately before any future authorized bind, and the container-side kernel mount-isolation guard must verify its RO corpus/RW scratch mounts on process startup. There remains a time-of-check/time-of-use gap between host witness and Docker bind creation. Do not automatically mount, restart Hermes, bind the entire `/nas` share, accept the false `/media/scott/NAS5` directory, or claim independent custody. **Production Hermes still has its original 16 mounts (9 writable); issue #414 and this PR remain HOLD.**

## October 24 superseding qualification warning

The physical result documented above is HISTORICAL for rootless entrypoint SHA256 `81add7ecd43ef1b3fccc5d8097b4f51ed196cfb61d5ccd3cd243e51a552ab6f5`. Current draft candidate source changed to SHA256 `a539208c578eb1cb3d80068207fda4dce09a4c31cc567aca56556ca0745133aa`, which refuses inherited `/app/hermes-home.defaults/config.yaml` and requires independent pre-provisioning. The historical physical result **does not qualify** the new script, actual image, external signing custody or any deployment. The physical canary explicitly refuses before Docker object creation until independently authorized requalification. The no-Docker source and local synthetic unit tests may still run. Production Hermes remains HOLD.
