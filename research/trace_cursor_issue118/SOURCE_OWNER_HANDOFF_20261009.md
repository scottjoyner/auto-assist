# Source-owner handoff: trace cursor and custody guard
Date: October 9, 2026. Scope: /home/scott/git/delm-sandbox/cursor-final-20261009 on x1-370.
Status: REVIEWABLE PATCH; PRODUCTION DEPLOYMENT HOLD.
Issue: https://github.com/scottjoyner/auto-assist/issues/118
Upstream GLiNER/collector research: askHR draft PR #1.
The operational collector, real source data, and NAS5 have not been modified.
Original live source SHA256: fba0d51b9a73c9de5f4a69705b2c325b18417cd8e051400e8c30ec31f3bcdbfe.

## Contents
- baseline-trace-spool-capture.py: exact frozen original.
- trace-spool-capture.py: updated isolated collector with no production installation.
- cursor_window_guard.py: pinned source snapshot, generation and complete-line proof.
- seal_index.py: one-pass local seal metadata index with fail-closed receipt checks.
- collector-release-candidate.patch: unified diff against frozen original, clean git apply check and byte-exact replay.
- sandbox_fixture.py: portable 9-test original collector hermetic fixture.
- test_candidate_collector.py: 19 synthetic capture() integrations against isolated code.
- spool_metadata_preflight.py: read-only ext4 spool metadata count and migration gate.
- SOURCE_OWNER_HANDOFF_20261009.md: owner acceptance and release status.

## Behavioral changes
1. Pin original source FD identity (device, inode) and byte size; fail if modified while reading.
2. Commit only through last complete LF record; preserve split UTF-8, never decode for cursor accounting.
3. Save original-byte SHA256 prefix and source identity with checkpoint; deny rotation, shrink, rewrite, legacy checkpoint, corrupt checkpoint and invalid offsets without rewinding.
4. One-pass local sealed-metadata index detects orphan archives, incomplete manifest/ready pairs, mismatched ready digest and sealed offsets newer than checkpoint. Hold instead of duplicating bytes after crash.
5. Flush/fsync archive, ready, manifest, sealed directory, checkpoint and checkpoint directory before acknowledging checkpoint persistence. No delete/prune of source logs.
6. If nothing is captured because of a hold, return nonzero with a concrete custody error.

## Evidence
- 9/9 existing baseline hermetic tests PASSED.
- 19/19 expanded candidate tests PASSED.
- Patch independently applied to a fresh copy of original; reconstructed result byte-identical, 19/19 replayed tests PASSED.
- Covers two appends/restart, torn line, inode swap, same-size overwrite, truncation, legacy state, pre-checkpoint crash, repeat capture, orphan archive, absent/altered ready marker, corrupt offsets JSON, symlink denial, fsync calls, and untouched original SHA.
- These process-level tests do NOT establish physical power-cut recovery or independently witnessed archive custody.

## October 9 production metadata-only preflight
- Spool is the local ext4 mount /dev/nvme1n1p1 at /media/scott/SSD_4TB, not NAS. No user trace payload, archive, or original source log was opened.
- Existing cursor state contains 152 legacy offset-only source entries and zero complete provenance entries.
- Preflight snapshot found 380 archived segments, 377 manifests, 377 ready markers, 710 legacy manifest source rows, and three archive files lacking both a manifest and ready marker. A later snapshot had 381/378/378 as the live producer created a normal new triple; the three archive-only anomalies persisted.
- Exact counts are time-sensitive. The migration cannot start until an operator reconciles all legacy cursor histories and the three incomplete archive groups without deleting/rewinding anything.
- One-pass sealed sidecar metadata indexing took 0.025 to 0.051 seconds across four read-only scans. Raw tar archives were not opened.
- This inventory is diagnostic only. It cannot prove past event completeness, archive content digest, or source generation across inode reuse.

## Mandatory gates before deploy / merge
1. Legacy production offsets and archived manifests lack these fields. No safe auto-migration. Owner must inspect current actual source UUID/physical ownership, establish a witnessed checkpoint against retained archived receipts, and explicitly approve migration. Enabling the patch without this will intentionally HOLD historical sources.
2. Power-cut/failure-injection and restored archive checksum plus ready/manifest matching on target filesystem remain unproven.
3. One-pass manifest indexing removes prior O(number of sources × number of seals) rescanning. Current local sealed metadata indexed in 0.025–0.051 seconds (four read-only measurements), but verify while the real producer and drainers run. Do not induce bulk NAS reads.
4. Confirm physical scheduler singleton flock, spool/drainer lock compatibility, and independent history preservation as separately gated in auto-assist #117. No user-data deletion/replay permitted.
5. Staging owner review, exact-head CI, rollout/rollback plan, and explicit production authorization are outstanding.
6. A local unsigned SHA receipt is not independent custody or source-generation proof across inode reuse; trust limitations must remain documented.

Decision: READY FOR REVIEW/ISOLATED STAGING; NOT PRODUCTION-READY. Keep issue #118 OPEN; do not deploy over live collector or claim recovered historical records.

