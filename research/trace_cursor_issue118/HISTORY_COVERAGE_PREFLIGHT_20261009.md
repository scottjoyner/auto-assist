# Issue 118: bounded source-history coverage preflight

**Observation:** October 9, 2026, 13:56 EDT, x1-370 local SSD_4TB ext4 spool. Draft/research only. No authority to migrate, deploy, trim, or rewrite source/archives.

## What changed
New modules under this PR:
- source_history_coverage.py — bounded, read-only JSON manifest/checkpoint census; opens no user source log, archive tar, or database member. Uses no-follow pinned regular metadata FD reads and rejects changed metadata, oversized sidecars, incomplete archive/manifest/ready triplets and negative offsets.
- test_source_history_coverage.py — 12 synthetic tests covering overlaps, positive gaps, initial missing prefix, timestamp reset, negative and Boolean cursor values, malformed rows, bounded spool and metadata symlinks.
- run_standalone_research_gates.sh — runs dependency-free synthetic tests for guard/index, history coverage, orphan integrity. Does not access the live spool.

## Observed local summary
- Stored cursor entries: **152**, all legacy without authenticated device/inode and original-byte prefix in the persisted checkpoint.
- Local sidecar manifests inspected: **419**; declared sidecar metadata bytes: **3,437,531**; claimed archival file interval rows: **823**.
- Current source-key intersection with local historical sidecars: **8**. Unmatched, nonzero-offset cursor source keys: **144**. Their group breakdown: runtime_traces 134, zcode_traces 7, dc_traces 2, console_events 1.
- Adjacent transitions under legacy size-at-open accounting: **17 backward overlaps/resets**, **0 adjacent positive forward gaps**.
- Sorted union of archival claimed intervals still identifies **one internal uncovered range**, and **two sources lacking an interval beginning at byte 0**.
- Legacy size_at_open is NOT a trusted committed cursor and these counts do not prove duplicate or missing historical source records.
- Source payload bytes opened: **0**; tar/archive payload bytes opened: **0**; raw source paths in output: **none**.
- Source-owner migration authority: **denied**.

## Independent local acceptance
- 12/12 history coverage synthetic tests pass.
- Guard/index synthetic suite: 15/15 pass.
- Orphan verifier synthetic suite: 7/7 pass on x1.
- Real synthetic capture(): 21/21 pass, fresh patch replay 21/21, original baseline 9/9.

## Next source-owner gates
1. Locate independently retained archive manifests/receipts for 144 unmatched current source keys, without recursively reading NAS or creating new copies on the nearly full SSD.
2. Establish exact original source generation and archived committed-end proof for each supported time interval; apparent overlap may be replay or rotation. Current claimed ranges are insufficient.
3. Reconcile three orphan archive-only segments and the independently verified SQLite content against signed or otherwise authoritative custody, then permit separately reviewed sidecar recovery only; never infer committed_end from size_at_open.
4. Validate power-loss/restore and scheduler/drainer exclusion on an authorized target ext4 test environment, then document rollback and source-owner migration decision.
5. Keep draft PR #208, #118, #117 and independent CI blockers #143/#145 open until the full release evidence exists.

**Do not use this preflight to adopt a production cursor or restart the collector.**
