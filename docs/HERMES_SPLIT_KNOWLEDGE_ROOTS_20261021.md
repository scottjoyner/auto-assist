# Hermes read-only knowledge / writable artifact split — candidate

2026-10-21. **Research-only: draft PR, HOLD, no production deployment or model dispatch.**

## Motivation and affected paths

The running production `assistx-hermes-adapter` has **16 mounts, 9 writable**,
including broad writable aliases of the SSD/knowledge evidence root and NAS5.
These aliases are the unresolved P0 risk in
[auto-assist issue #414](https://github.com/scottjoyner/auto-assist/issues/414).
No production mount, image or process was changed by this candidate.

The existing adapter uses `KNOWLEDGE_ROOT` for both corpus context reads and
self-task artifact writes. It also writes model ENV notes, an evaluation JSON
registry and a semantic index. Simply making the corpus read-only therefore
breaks legitimate output while preserving a dangerous implicit fallback.

## New opt-in configuration contract

In a future **independently reviewed**, restricted deployment, both new
variables must be provided together:

```text
HERMES_KNOWLEDGE_READ_ROOT=/research/knowledge
HERMES_KNOWLEDGE_WRITE_ROOT=/workspace/knowledge
HERMES_KNOWLEDGE_ROOT=/workspace/knowledge
HERMES_EVAL_PATH=/workspace/knowledge/eval.json
```

Legacy behavior is preserved when **both** new variables are unset. When
split mode is enabled, startup rejects one-sided values, relative paths,
non-normalized paths, symlinked roots, overlapping/aliased roots, and an
evaluation registry outside the writable root. It does **not** provide
authority to expose any real host path. The container mounts are an
independent, essential security boundary.

The real Hermes adapter uses READ_ROOT for bounded top-level / one-level
nested MD/TXT context inputs and skips symbolic-link entries. It uses
WRITE_ROOT for self-task artifacts, model scratch/ENV notes, and artifact
verification; its evaluation JSON must also be under WRITE_ROOT.
The semantic-memory index is stored in the WRITE_ROOT `.memory` directory,
while index input is enumerated only from READ_ROOT. New writes avoid
following symlink leaf paths; the eval registry uses a generated atomic
temporary file in split-root mode.

## Evidence actually obtained on x1-370

Checked out the latest remote `auto-assist/main` at
`1cf471eeac0c7d7f7a43b5f759c39e3502f23b95` into a *separate worktree*
(`fix/hermes-split-knowledge-roots-20261021`). The live deployment checkout
`/home/scott/git/auto-assist-deploy-20261016` and existing local main
were left unchanged.

Focused no-network regression checks:

```sh
PYTHONPATH=src python3 -m pytest -q \
  tests/test_hermes_split_knowledge_roots.py \
  tests/test_hermes_delegated.py \
  tests/test_hermes_memory_provider.py \
  tests/test_hermes_external_config.py
```

Explicit opt-in physical Docker check (x1-370 only):

```sh
python3 tests/hermes_split_root_container_canary.py --physical-disposable
```

This physical test creates a random, disposable **offline** overlay on the
exact still-running Hermes image. It overlays only the three reviewed Python
source files into the image; no source checkout or SSD/NAS path is mounted.
The container runs as UID 1000, with no network, read-only rootfs, dropped
capabilities, no-new-privileges, tight resource limits and only a read-only
synthetic corpus plus writable synthetic scratch mount. The **actual patched**
adapter is imported in two fresh Python processes; LLM output is intercepted
using an explicit test-only stub, no provider or real agent task is invoked.

The first run reads synthetic context and writes a self-task artifact and
evaluation record to scratch; the second verifies persistence and increments
the synthetic evaluation history. The test refuses unexpectedly changed
production image identity, validates container effective mount/security
metadata before execution, and removes its temporary Docker images/container.

**Important scope limit:** This is direct `python3 -B /workspace/smoke.py`
execution, **not** a normal-entrypoint Hermes activation proof for the new
source. The previous agent-harness PR #18 normal-entrypoint and restricted
Compose checks used an older image/source, and cannot be promoted to release
evidence for these new changes.

## Remaining HOLD gates

- Independent security review of symlink races and mount protection;
  same-host path checks are not rollback-resistant custody.
- A newly source-attested image, complete normal-entrypoint check and
  reviewed Compose candidate carrying both opt-in env vars.
- Real-data compatibility after scoped read mounts have been approved.
- Specific review/containment of existing RW SSD aliases, NAS5 and mutable
  repository mounts, including genuine read/write ownership requirements.
- Owner-bound task receipts, external revocation/fencing and rollback
  controls, plus hosted CI and an independent approver.

Do **not** merge or enable this code in the production Hermes service on the
strength of a synthetic test. **Issue #414 remains open and deployment HOLD.**

## Follow-up qualification (October 22, 2026)

The earlier direct-Python qualification remains true for its original
source/test point. A later source-identical, restricted Docker Compose
normal-entrypoint research canary is documented in
[HERMES_SPLIT_ROOT_NORMAL_COMPOSE_20261022.md](HERMES_SPLIT_ROOT_NORMAL_COMPOSE_20261022.md).
It has synthetic-only normal strict-executor restart acceptance, not
production ingestion or owner-authorized runtime acceptance. HOLD remains.
