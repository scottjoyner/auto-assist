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

## Follow-up: signed synthetic corpus

An opt-in Ed25519-signed bounded MD/TXT corpus admission was added for restricted synthetic checks. See [HERMES_ED25519_CORPUS_CUSTODY_HOLD_20261022.md](HERMES_ED25519_CORPUS_CUSTODY_HOLD_20261022.md) for positive/negative physical evidence, signer-custody limitations and replay/TOCTOU blockers. Deployment remains HOLD.

## October 24: opt-in container-side remote output backing-source check

A remaining failure mode exists even if host read-only preflight confirms a valid `/nas` CIFS mount and both Docker Compose bind sources use `create_host_path:false`: **an already-existing local directory can be mounted** if remote storage disappears between the host check and the Docker bind. The original `verify_mount_admission()` inspected exact read-only input and separate writable output mount points, but did not inspect the *backing filesystem/source* from Linux `/proc/self/mountinfo`.

The existing `src/assistx/knowledge_roots.py` strict isolation gate now supports two **optional, paired** environment labels for a **future** restricted, independently authorized network-backed output profile:

    HERMES_EXPECTED_WRITE_FSTYPE=cifs
    HERMES_EXPECTED_WRITE_SOURCE=//192.168.1.202/fileserver

These are **an expected identity example, not authorization** to use the share, a directory, a credential, a grant, or a Docker Compose bind. Both variables must be supplied together, and they require `HERMES_REQUIRE_MOUNT_ISOLATION=true` plus explicit split roots. The new check matches the exact configured writable mount's bounded **kernel mountinfo** `fstype` and backing `source`; it rejects any mismatch (such as local ext4, tmpfs, autofs without mounted CIFS, NFS, a different share or server), bad requested CIFS notation, or duplicate/ambiguous/missing mount evidence. The pre-existing RO input/RW output polarity, distinct IDs and nested-mount checks still apply. It never reads output content, opens production evidence, performs a network request, creates paths, or mounts storage. Absence of these two new settings preserves legacy and existing synthetic profile behavior.

The synthetic disposable strict-executor test remains on *local tmpfs* fixtures, so it deliberately does not set a real CIFS expectation or claim a remote filesystem test. Its exact source-blob pin is updated to the new version of `knowledge_roots.py`, preventing future physical canaries from silently executing an older verifier. Offline generated mountinfo records test correct matching and negative local-disk/fake-share/malformed configuration outcomes.

**Limitations remain material:** the expected source labels are caller-supplied and are not independent proof of the share's owner, ACLs, credentials, grant or retention; mountinfo alone does not remove privileged-host races or assure reliable remote writes during a disconnect; Linux may present varied mountinfo sources across namespace/kernel versions. Future approved candidates must physically qualify the actual container mountinfo identity under the exact image and Docker runtime, then prove output integrity after network loss and restart. No current production Compose profile, environment, image or running Hermes container is changed. Issue #414 and PR #425 remain **HOLD**.

## October 24 07:00 ET: covered output paths revalidate the remote backing at use time

The previous opt-in `HERMES_EXPECTED_WRITE_FSTYPE` and `HERMES_EXPECTED_WRITE_SOURCE` admission validated CIFS identity **once at strict startup**. A privileged host or Docker mount-namespace change *after startup* could replace an approved writable bind with an existing local `ext4` directory before a later Hermes output write. Merely caching the startup result was not adequate for this failure mode.

`src/assistx/knowledge_roots.py` now calls `_recheck_remote_mount_before_write()` inside the existing `checked_write_path()` safety wrapper. It activates **only** when either expected remote identity setting is present, requires both settings plus strict mount isolation, all explicit read/write root paths and write mount, and calls the full `verify_mount_admission()` again. This checks the bounded current kernel `/proc/self/mountinfo` against the separate RO input and RW output mount boundaries, no nested mounts, and the pinned CIFS backing source. Missing or partial settings, a replaced kernel mount record, a missing write bind or changed share fail closed with `ValueError` before the covered output-path operation proceeds. Without the new identity settings the existing legacy/synthetic behavior is unchanged.

Synthetic read-only tests use temporary directories and in-memory generated kernel mountinfo: a valid CIFS record passes on one checked output path, a subsequent local `ext4` source is rejected, and a restored CIFS record requires a fresh check (not a cached pass). Additional negative tests drop each required setting separately; no target output file is created in those cases. The disposable normal-entrypoint canary source-blob pin has been updated to the exact new implementation. **No real share was unmounted or interrupted, and no production Hermes task was started.**

**Limitations:** this recheck applies to paths that call `checked_write_path()`; it is not a comprehensive mandatory interception of every filesystem write or an operating-system-level write fence. It does not detect a CIFS network outage where the mount remains present in mountinfo, prove durable writes after a disconnect, close the check-to-open TOCTOU window, attest independent source custody, or approve `/nas` output. A future production candidate requires a controlled, physically evidenced network-loss/restart test with independently approved narrow binds, image provenance, owner grants and output integrity. The current production Hermes image and 16 legacy binds (9 RW) remain untouched; issue #414 and PR #425 are **HOLD**.
