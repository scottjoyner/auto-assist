# Scoped v1 OpenCode trace export — 2026-10-09

**Status: draft / research only.** No admission, routing, execution or provider authorization changes. This change is based on auto-assist main `a300072d11787a58a4587fa325f1b8fd66803730` and deliberately preserves existing `opencode-session-trace/v1` record fields rather than importing an earlier, much larger generated v2 exporter.

## Hypothesis
Requesting a single execution's receipt should never pull the entire OpenCode session index and all unrelated message/part histories into memory. Exports must be strictly read-only to the live SQLite database, deterministic under a bounded scope, and private/durable as JSONL. Failed scopes or output writes should not destroy previously generated evidence.

## Implementation
- Open SQLite with `mode=ro` and `PRAGMA query_only=ON`. Apply optional `--session-id`, literal `--title-prefix` and `--max-sessions` predicates inside SQL before fetching; default 500, absolute maximum 5000. Reject a scope that exceeds its cap instead of silently returning the first page.
- Bound each selected session's message and part reads to **10,000** records, then fail closed on overflow. Objective text hashing also reads at most 10,000 parts. Existing v1 record schema/identity interpretation remains unchanged.
- A missing requested session, invalid scope, database error, unsafe symlink output or non-JSON-serializable record returns failure **without overwriting the previous receipt**.
- On success, write a private 0600 temp file in the destination directory, `fsync`, then atomically replace the designated output. No prompts, complete tool payloads, API tokens or provider calls are introduced.

## Evidence
- Existing plus newly added regression suite: `python3 -m pytest -q tests/test_export_opencode_trace_scope.py tests/test_export_opencode_session_traces.py tests/test_free_subagent_supervisor.py` — **44/44 PASS on x1-370**.
- Real 1.18.35 OpenCode SQLite database (~549 MiB at investigation), read-only scoped query for *only* known canary `ses_edfac7c37ffeM2vtohHDhzFxsv`: **one v1 JSONL record** (model `cohere/north-mini-code:free`, OpenCode-recorded cost 0), file mode **0600**. This is NOT an independent provider billing receipt.
- The code has no ability to prove signed event lineage, graph admission, producer-owner custody, allocator revocation or upstream provider spend; those remain explicit release gates.
- Swift/physical iOS and Vitrial Space Bunny workstreams are not part of this PR.

## Remaining gates
Independent node replay, current-head GitHub Actions CI, code-owner review and a staged authenticated trace UI compatibility/negative test pass before enabling any export automation. Do not merge this v1 patch with the unreviewed generated v2 experiment as a single PR; reconcile separately.

## Cross-node offline validation — 2026-10-09 EDT
- Copied the commit onto a separate xwing Git worktree, without changing live OpenCode or the Vitrial Space Bunny executor.
- Initial xwing test run: **40 pass, 4 fail** — the unrelated legacy `free_subagent_supervisor` tests read xwing's live OpenCode catalog instead of their supplied fixture. This is an existing fixture-discovery limitation outside the narrow trace-exporter change.
- Re-ran with `OPENCODE_BIN=/definitely/nonexistent-opencode` so the supervisor uses the bundled offline fixtures, and with a **process-local** `GIT_CONFIG_COUNT` override to disable test-repository commit signing (no global Git config change): **44/44 PASS** on xwing. The same suite was **44/44 PASS** on x1-370 without those workarounds.
- Trace-specific tests do not need the model-catalog override. Fixing global fixture precedence remains separately scoped; this PR changes only trace export behavior and tests.
