# Issue #118 — source-owner acceptance delta (October 9, 2026)

**Disposition: candidate implementation validated; historical migration and release remain HOLD.** This is a read-only investigation of x1-370's local ext4 spool, not a credentialed archive ledger, checkpoint reanchor, or deployment authorization. See draft PR #208.

## Current source-window witness
- 152 stored legacy cursor entries (all offset-only; zero prior authenticated generation receipts).
- Inspected 152 source files with a bounded read-only scan, hashing 40,402,723 committed-prefix bytes without writing or displaying trace payload content.
- All 152 stored offsets ended at a newline; all FD identities/size/mtime/ctime remained stable within each file read; the cursor state file remained byte-identical during the scan.
- The 152 private source-prefix digests and source inode/device identities remain **local-only** at `/home/scott/git/delm-sandbox/cursor-final-20261009/legacy-cursor-witness-private.json` (mode 0600). They have not been uploaded to GitHub. They are observational, NOT an adopted checkpoint or proof of history completeness.
- Running producers change file sizes, so instant size/offset differences are not authoritative across separate scans. A metadata snapshot showed two append deltas totaling 24,398 bytes; an earlier snapshot also showed one then-current offset above file size.

## Historical local sidecar lineage
- Of 152 current cursor source keys, only **8** match source keys seen in the current local sealed-manifest metadata; **144** have no matched local archived sidecar row. This does not prove their history is missing from all retained stores.
- Among the matched historical intervals, **15** adjacent archived ranges were noncontiguous by their recorded offset_from and size_at_open. These require owner review; legacy torn-line capture moved cursor through incomplete data, so historical content cannot be reconstructed from current position alone.
- Full historical archival/restore evidence remains in issue #117, not implied by this forward-cursor witness.

## Three orphan archive-only files
- Three compressed archives lacked both external manifest and ready receipt; no files were deleted or overwritten.
- Each passed `zstd -t` and tar listing; each contained an internally readable schema-v1 manifest.
- Hash-and-size checks for **all eight archived, redacted JSONL members** matched their internal file manifests (3+3+2). No raw JSONL text was displayed or saved by those checks; compressed archives were read only.
- Six controls from normal archived triples demonstrated exact, deterministic reconstruction of external manifest metadata from the internal archived manifest plus compressed archive SHA-256, size, and filename (6/6 exact matches).
- Orphan manifests lack offset_end_committed and trustworthy source generation, so their sidecars have **not** been recreated in the production spool. Even if recovered, historical cursor migration remains blocked until retained-archive provenance and independent restore/custody are witnessed.
- Aggregate integrity evidence is stored locally, separately from the production spool.

## Candidate code acceptance
- Baseline hermetic source acceptance 9/9; candidate 19/19; clean patch replay 19/19, all passing.
- One-pass local seal metadata index was observed at 0.025–0.051 seconds per scan on the live local ext4 spool, with no archive payload reads.
- The installed collector SHA256 remained `fba0d51b9a73c9de5f4a69705b2c325b18417cd8e051400e8c30ec31f3bcdbfe`; **no collector was deployed**.
- GitHub PR #208 exact-head CI is **red**: unit job reports 77 failed, 669 passed, 59 deselected; recovery-canary job 1 failed, 11 passed. Errors include repository source-binding schema mismatches and missing from_contract_payload. No failing stack trace in the retrieved unit log references this research-only path. Do not presume the whole-repo tests are green or the failure is proven preexisting without a verified main-head comparison.

## Closure gates and owner actions
1. Reconcile the 144 currently unmatched cursor source keys with independent retained archives or source-owner historical audit, and investigate the 15 noncontiguous archived intervals. Preserve all retained bytes, no automatic rewind or delete.
2. Stage deterministic orphan sidecar repair only under source-owner custody review, verifying the internal manifest and all corresponding members and requiring an independent witness; **do not infer committed-end from size_at_open**.
3. Approve a migration decision for the 152 hashed, newline-aligned source prefixes only after archive evidence is matched and source generation is independently witnessed. Revalidate atomically because the live producer continues writing.
4. Prove target-filesystem power-cut/restart and archive restore (including ready/manifest/checkpoint ordering), source fencing/rotation policy, and rollback on real scheduler/drainer workflow.
5. Obtain exact-head CI disposition, merge review and explicit rollout approval. Do not mark #118 resolved because synthetic tests passed.

**Authority boundary:** research-only PR #208 stays draft, issue #118 stays open. No producer, source log, state checkpoint, NAS5, or production archive mutated.
