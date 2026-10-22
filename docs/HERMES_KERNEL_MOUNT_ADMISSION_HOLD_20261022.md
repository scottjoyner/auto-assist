# Hermes strict kernel mount admission — October 22, 2026

**Research-only / HOLD** for auto-assist draft PR #425 and P0 #414. No production service, mount, file corpus, credential, or provider was changed or inspected.

## Concrete failure being closed

A directory can exist under /media/scott/NAS5 on x1-370 while the intended NAS filesystem is **not** mounted there. A directory-exists check is therefore insufficient to authorize Hermes to use that path. Previously the adapter's opt-in read/write root separation checked only canonical paths, aliases and overlap, not actual kernel mount isolation.

This patch adds a second, opt-in strict startup guard to the production adapter's existing split-root parser. Existing legacy behavior stays unchanged when the new flag is unset. Future restricted profiles explicitly set:

    HERMES_KNOWLEDGE_READ_ROOT=/research/knowledge
    HERMES_KNOWLEDGE_WRITE_ROOT=/workspace/knowledge
    HERMES_KNOWLEDGE_WRITE_MOUNT=/workspace
    HERMES_REQUIRE_MOUNT_ISOLATION=true
    HERMES_KNOWLEDGE_ROOT=/workspace/knowledge
    HERMES_EVAL_PATH=/workspace/knowledge/eval.json

On each fresh adapter process import, strict mode reads **/proc/self/mountinfo** and requires an **exact read-only mount** at the configured knowledge read root and a **different writable mount** at the configured scratch mount, containing the output root. It rejects rootfs fallbacks, missing binds, RO/RW reversal, duplicate mountpoints, nested mounts beneath either restricted root, overlapping path boundaries, missing directories and symlinked component aliases. Ambiguous or too-large mountinfo fails closed. No host file contents are read, and no SSH/NAS mount or protected source is used.

The guard is a **kernel containment check only**: its presence does not authenticate the host-side source, independently sign image provenance, guarantee a trusted external custodian, or remove the production Hermes instance's broad writable SSD/NAS/Git/SSH mounts. It does not prevent a sufficiently privileged host administrator from changing the underlying Docker environment, and separate review and renewal remain required.

## Reproducible proof

The restricted synthetic Compose profile and strict verifier now include both new environment variables. The test builds an offline overlay containing the actual patched code on the unchanged running Hermes image, then runs the normal rootless /entrypoint.sh and assistx.strict_executor_main in a fresh networkless, read-only-rootfs, UID-1000 container. The kernel admits only a synthetic read-only corpus bind and a separate synthetic writable workspace bind.

    PYTHONPATH=src python3 -m pytest -q tests/test_hermes_mount_admission.py
    python3 tests/hermes_split_root_normal_compose_canary.py --physical-disposable-compose --json

Negative unit tests inject generated mountinfo records and cover missing input/output, read/write polarity swaps, nested bind mounts, duplicate mountpoints, same mount ID, missing dirs, symlink aliases, bad env modes and unmounted fallback directories. Positive physical tests run two normal strict-executor entrypoint starts, read synthetic corpus using the actual patched adapter, persist a synthetic self-task/eval result to isolated RW scratch, and confirm survival of the second start. Test-only HTTP calls and model execution are mocked or prohibited. The disposable container/image and scratch are removed. Normal run reports **HOLD** even if all checks pass.

## Release decision remains HOLD

Original production Hermes continues running untouched with 16 binds and nine writable mounts, including two paths exposing the protected SSD knowledge evidence. Beelink's three observed storage filesystem mountpoints are not automatically approved Hermes input/output. Any genuine production migration still requires explicit narrow RO corpus selection, independent custody/signatures, separate RW scratch authorization and capacity/rollback, task receipts, revocation and fencing, and independent reviewer approval. Keep P0 #414 open and PR #425 draft.
