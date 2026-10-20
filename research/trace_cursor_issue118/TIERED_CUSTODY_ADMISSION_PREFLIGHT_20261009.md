# Issue #118 — two-tier custody and NAS admission preflight
Date: October 9, 2026. Scope: x1-370 research-only. PR #208 remains draft; issue #118 remains open.

## Why this gate is needed
The previous 144 unmatched source cursors were compared only with local sealed sidecars. The fleet knowledge index identifies a separate permanent NAS archive under `/nas/auto-fintune/traces/archive/YYYY-MM/`, but the available local NAS archive inventory is cached and not equivalent to authenticated per-source custody. This pass joins **local aggregate counts** with the **local cached inventory**, without contacting NAS or reading raw source files.

## Observed immutable inputs
- Source: `/media/scott/SSD_4TB/finetune-trace-spool/state/archive-inventory.json`, checked at `2026-10-09T08:36:58Z`.
- Cached inventory: 1,002 archived segments, 177 inbox segments; 60 SHA-256 verified in inventory, 942 not marked SHA-verified. Inventory state: `problems`; one recorded capture hole. These are cached verification states, not direct proof any unverified file is corrupt.
- Local history census: 152 legacy source cursor entries and 144 nonzero-offset source keys without an exact local sealed-sidecar match.
- Split: **134 runtime_traces** sources have neither local per-source sidecar match nor a listed group in the cached NAS archive inventory; **10 other sources** have no local source match, but do have *group-level* NAS inventory evidence. Group-level presence is not per-source evidence.
- The archive inventory explicitly reports `runtime_traces` and `token_usage` among its `groupsNeverSeen`; cached inventory is time-bounded and may lag the live source/collector. Do not assert these groups are absent from current remote NAS solely on this basis.
- Local spool sits on ext4 `/dev/nvme1n1p1` with ~147 GiB available and 96% capacity used. No new archive copies were made.

## Current drain and custody status
- Safe drain state at local status snapshot: `held_admission`, 257 pending files, `automatic_cleanup_permitted=false`, `local_source_release=BLOCKED`.
- Watchdog snapshot at `2026-10-09T18:47:01Z`: warning, 433 local backlog items, consisting of **260 unacknowledged** plus **173 retained after acknowledgment**. These counters have different meanings than safe drain's 257 pending files; do not add them together.
- Watchdog reports current NAS L1 admission failure and a long-running concurrent bulk `rsync`. The process existed when checked, but forward progress was **not proven**, and no service or process was stopped.
- The previous `drain-status.json` reported success at `2026-10-08T15:05:17Z`, which must not override newer safe-drain/watchdog HOLDs.
- Source release, automatic cleanup, direct NAS writes and cursor migration remain prohibited.

## Reproducible research artifacts
- `tiered_retention_triage.py` and `test_tiered_retention_triage.py`: group-level vs source-level evidence separation, 9/9 synthetic tests.
- `safe_drain_readiness.py` and `test_safe_drain_readiness.py`: fail-closed read-only NAS admission/custody preflight, 10/10 synthetic tests.
- Both readers use bounded pinned metadata reads from the existing `source_history_coverage.read_bounded_json` helper. Neither contacts NAS.
- `run_standalone_research_gates.sh` now includes both test suites.
- `tiered-retention-local-aggregate.json` and `safe-drain-readiness-aggregate.json` contain only group counts and summary verdicts, not source paths, user trace contents or credentials.

## Smallest actionable next gates
1. **Storage owner:** identify and resolve the current NAS L1 admission condition and competing bulk writer; do not bypass the gate, clear acknowledgments, or terminate the writer without independent evidence and explicit authorization.
2. **Retention owner:** use a *bounded, independently witnessed* archive inventory refresh only after storage is healthy, with source-group and per-source identity visibility. Priority 1: 134 `runtime_traces` entries; Priority 2: the other 10 unmatched current cursors.
3. **Collector owner:** preserve the 152 private LF-aligned forward-source witness, and reconcile historical interval resets, one internal claimed coverage hole, two missing initial intervals, three orphan segments and their source-generation history separately.
4. **Release reviewer:** keep PR #208 draft. Synthetic code tests can pass while production migration fails: source custody, target-ext4 physical crash and restore, exact-head CI and rollback are still mandatory.

**Authority boundary:** observational and research-only. No original source, checksum, manifest, NAS object, archive, process, checkpoint, or producer was changed by this pass.

