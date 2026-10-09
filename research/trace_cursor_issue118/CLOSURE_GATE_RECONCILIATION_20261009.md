# Issue #118 — bounded closure gate reconciliation (October 9, 2026, 12:30 ET)

**Review-only draft PR #208. The installed collector, producer, source logs, checkpoints, and NAS data remain unchanged.** Exact installed collector SHA256: `fba0d51b9a73c9de5f4a69705b2c325b18417cd8e051400e8c30ec31f3bcdbfe`.

## Verified engineering acceptance
- Frozen original source sandbox: **9/9 pass**.
- Candidate isolated capture() integration: **21/21 pass**.
- Fresh git-apply of the unchanged patch against the frozen original, independent test rerun: **21/21 pass**.
- Added concurrency race tests: append during a pinned read and same-size rewrite during a pinned read each raise a fail-closed custody hold.
- Existing ff/fdatasync failure injection, recovery, symlink, source-generation, archive receipt consistency, and two-group single-index acceptance remain green.

## Forward-source baseline evidence, not adoption
- Production ext4 local spool: 152 legacy source cursor entries, all offset-only.
- Bounded read-only witness hashed exactly **40,402,723 committed-prefix bytes**, with **152/152 LF-aligned offsets** and **152/152 stable source-FD metadata checks**; the stored state file did not change during the observation. Duration 0.077 s.
- This is a current-source baseline, **not a historic no-loss certificate** or permission to change checkpoints. A private per-source witness is confined to x1-370 at `/home/scott/git/delm-sandbox/cursor-final-20261009/legacy-cursor-witness-private.json` with `0600` permissions, not committed to GitHub.
- Only **8/152** current cursor keys are represented in this local spool's archived sidecars. All **144** unmatched sources have nonzero stored offsets, so they cannot be waived as empty files.
- The 15 prior adjacent noncontiguous sidecar transitions were all **backwards overlaps / apparent resets**, not direct positive gaps. Nonetheless, merging intervals reveals **one represented source with an internal uncovered range** and **two whose first archived range starts after byte zero**. No exactly-once or historical completeness claim is supported.

## Three orphan archive-only groups
- All three passed compressed stream verification, tar directory checking, and internal schema-v1 manifest decoding.
- Their **eight redacted JSONL members passed 8/8 SHA256 and size checks** against embedded metadata, without exposing contents.
- Six intact archive controls recovered exactly their external sidecar dictionary from embedded manifest + compressed archive SHA256/size/name (**6/6**).
- **No external sidecars were recreated** and no cursor was advanced: legacy manifests cannot authenticate an offset_end_committed or original source generation.
- A separate deeper SQLite-member restore probe could not be completed; do not imply DB restore acceptance.

## CI attribution
- The latest previous exact-head CI for PR #208 failed broadly: 77 failed/669 passed/59 deselected in unit job and 1 failed/11 passed in recovery-canary; no observed failing stack referred to this research path.
- Independently, a pinned untouched `main` checkout at `a300072` reproduced a `RepositorySourceBinding.from_contract_payload` failure in `tests/test_repository_source_binding.py` (1 failed, 5 passed, run stopped at first failure). This **proves that specific error exists on main**, not that all CI failures predate PR #208.
- The repository-wide baseline fix is separately tracked in [#143](https://github.com/scottjoyner/auto-assist/issues/143) and [#145](https://github.com/scottjoyner/auto-assist/issues/145). Do not relax fail-closed auth or source-binding rules to make CI green.

## Release decision
**Code/test review nearly complete; production closure HOLD.** Before enabling a migrated checkpoint, require source-owner no-delete reconciliation for the 144 unmatched sources and the covered-source union exceptions, independently witnessed source generation and retained-archive custody, approved orphan sidecar repair with committed-end evidence, target-ext4 fault/power-cut and actual restore proof, scheduler/drainer fencing and rollback, current-head CI disposition, and explicit deploy authorization. Full historical audit remains separately covered by issue #117. Keep PR #208 draft and issue #118 open.
