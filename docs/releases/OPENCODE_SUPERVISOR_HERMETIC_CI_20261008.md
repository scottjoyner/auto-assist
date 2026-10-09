# CI baseline repair: hermetic OpenCode supervisor observations
Date: October 8, 2026 EDT
Parent: Offline Safety RC2 draft #155. No production rollout or hosted-model calls.

## Prior hypothesis
- Two failing CI tests in `tests/test_free_subagent_supervisor.py` depended on the runner's real `$HOME/.local/share/opencode/opencode.db`. The test `discover_live_sessions(query_only=True)` was correct to report that a missing file is not a real session-history observation; the test wrongly assumed a local database would exist.
- The exporter integration test could create `tests/fixtures/empty_trace_exporter.py` in the repository and included a HOME-dependent branch, so different CI agents could produce different evidence or modify the checkout.
- Replacing both with disposable SQLite and Python exporter fixtures would preserve query-only behavior while giving consistent tests on a clean runner.

## Implementation / bounded behavior
- `test_discover_live_sessions_readonly_query_only` now creates an empty synthetic OpenCode-compatible `session` table in an isolated pytest temp path, injects only the database URI, and proves SQLite `PRAGMA query_only` and SQL writes fail. No user HOME or live session data is read.
- New `test_discover_live_sessions_missing_db_is_not_silently_empty` proves that a missing RO SQLite file yields `sqlite3.OperationalError` and never creates a directory. This must remain visibly unavailable rather than a fabricated healthy zero.
- `test_projection_with_trace_exporter_integration` creates a synthetic DB and tiny export stub under pytest tempdir; the stub returns an empty list and the read-only projection records count=0, sample=[], summary={}, with no `trace_exporter_error`. It does not create a fixture under the Git repository.
- No changes to `scripts/free_subagent_supervisor.py`, provider routing, authentication, quota admission, fleet tasks, filesystem deletion, or database persistence.

## Observation / acceptance
- Baseline CI on RC2 #155: supervisor `test_discover_live_sessions_readonly_query_only` failed with `sqlite3.OperationalError: unable to open database file`; `test_projection_with_trace_exporter_integration` failed because trace fields were absent when no HOME database existed.
- Isolated x1-370 checkout derived from RC2 `0e7e09f80c9686bf968d6c1c60f24b1b39843c3e`: focused `pytest` 3/3 passed and the entire `tests/test_free_subagent_supervisor.py` suite **34/34 passed**. `git diff --check` passed.
- Full GitHub CI is a separate acceptance gate, and failures outside these two cases (enhanced dashboard, secret-containing tracked env files, runtime catalog and other tests) are NOT claimed fixed by this slice.

## Security finding requiring separate custody
- Two root `.env`-style files are tracked even though the repository's security tests explicitly forbid non-template environment snapshots. Value-free inspection identified nonempty secret-like configuration fields. Do not paste contents into PR diffs or logs. Dedicated P0 issue #156 tracks review, credential rotation, safe template replacement and history exposure. Neither file is touched by this CI fixture PR.

**Decision:** keep this repair draft until the targeted full CI suite passes. No production authority changes or live OpenCode reads.
