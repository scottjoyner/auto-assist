# Issue 118 — orphan database content recovery witness
Date: October 9, 2026. Scope: local x1-370 ext4 sealed archive metadata and bounded in-memory SQLite checks.

## Verifiable result
- Three orphan archive-only groups are intact zstd/tar, and their eight redacted source members matched SHA-256 and recorded size.
- Six orphan database records are present: two physical database members and four references to previous snapshots.
- The references and embedded records resolve to **three distinct physical SQLite content digests** available in the current local sealed archive set: two physically embedded in the orphans and one in an existing complete archive.
- All three physical databases independently passed stored size, SHA-256 and in-memory SQLite PRAGMA quick_check. Total unique physical DB bytes checked: 97,902,592 bytes.
- All six recorded database content digests are recoverable from these verified physical database bytes.
- Of the four original reference_segment pointers, three uniquely matched current local archive filenames by segment identifier, while one pointer did not. An unresolved original pointer does not prevent content-addressed reconstruction, but it **does** prevent claiming complete historical reference-chain custody.
- The old orphan manifests still lack authenticated offset_end_committed and source generation; no sidecars can be installed or checkpoints advanced on this evidence.

## Reproducibility and tests
- orphan_db_restore_checks.py: inspect physically embedded orphan DB members without writing DB copies; 2/2 in-memory SQLite quick_check.
- orphan_content_resolution.py: metadata index of local complete archive sidecars plus orphan manifests, bounded physical member extraction and in-memory SQLite integrity checks. Reports aggregate results only.
- test_orphan_content_resolution.py: six synthetic tests, including valid DB, mismatched content, forged digest, malformed receipt, content reference resolution and missing content fail-closed. 6/6 passed locally.
- A separate orphan_integrity_review.py verifier and tests confirm member byte limits and tar path filtering.
- All raw content stays within memory on x1-370, not in this report or any GitHub draft. No user traces or database records displayed.

## Status and limitations
This is a **content-recovery acceptance** for six database records, not a certified source-to-spool-to-NAS chain of custody, time-ordered archive replay, physical power-cut recovery, or signed migration checkpoint. Retained archive #117 reconciliation, 144 unmatched source histories, interval union exceptions, source-owner attestation and exact-head CI remain mandatory.

Disposition: orphan DB physical content verification PASS; historical pointer chain PARTIAL; production sidecar repair and cursor migration HOLD.

