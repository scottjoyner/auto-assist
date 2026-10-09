# Trace cursor custody research candidate — issue #118

**Draft research only. Do not deploy or auto-wire this collector.**

This directory is a review-only reproduction of the local X1-370 collector safety patch. It intentionally does not include a copy of the operational collector because that source currently lives outside the auto-assist repository. The original remains unchanged.

## Patch contents
- collector-release-candidate.patch: diffs against SHA256 fba0d51b9a73c9de5f4a69705b2c325b18417cd8e051400e8c30ec31f3bcdbfe.
- cursor_window_guard.py and seal_index.py: source-window pinning and one-pass local seal metadata indexing.
- sandbox_fixture.py and test_candidate_collector.py: isolated synthetic acceptance tests.
- run_cursor_acceptance.sh: replays the above against an operator-supplied byte-identical baseline.
- SOURCE_OWNER_HANDOFF_20261009.md: evidence, limitations, migration holds.
- spool_metadata_preflight.py: read-only fleet diagnostic. Run only on the intended x1 spool host. No archives or raw trace logs are opened.

## Reproduce on an authorized staging host
Copy this directory into a writable scratch location. Copy the authorized original collector to **baseline-trace-spool-capture.py** only after checking its exact SHA256 shown above; never copy actual trace payloads. Then run bash run_cursor_acceptance.sh. The script applies the patch into a disposable temporary directory and requires byte-exact source equivalence.

Observed x1-370 synthetic results: original 9/9, candidate 19/19, fresh patch replay 19/19, all green. Production spool metadata preflight: 152 legacy offset-only cursor entries, 710 legacy manifest file records and three archive-only orphans. Those are explicit migration/custody holds.

## NOT accepted
No automatic source migration or cursor rewind; no orphan archive deletion or reconstruction; no production deploy, live restart, NAS writes, authenticated source restoration, signed independent archive witness, physical power-cut proof, or release activation. Source owner must decide the reconciliation and rollout plan separately. Keep #118 open.

See https://github.com/scottjoyner/auto-assist/issues/118 and related historical recovery #117.