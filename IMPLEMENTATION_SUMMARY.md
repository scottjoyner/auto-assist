# OpenCode trace receipt repair — October 9, 2026

**Status: draft, review-only. No production promotion, dispatch or merge is authorized.**

## Objective and hypotheses
A read-only OpenCode exporter should be able to select a bounded set of sessions on a real SQLite database, produce stable sanitized JSONL receipts, and never infer cost, retries, worktree ownership, or free-provider billing it cannot prove. Repeated exports of different databases must not suppress matching opaque session IDs.

## Implementation
- SQLite is opened by `file://...?mode=ro`, with `PRAGMA query_only=ON`. Session ID and literal title-prefix SQL predicates execute **before** fetching rows. A configurable hard cap (default 500, maximum 5000) denies accidentally scanning a huge corpus.
- OpenCode's millisecond `time_created` values no longer undergo incorrect seconds-based conversions. There are no user-prompt plaintext fields in exported receipts; only a SHA-256 digest and length of the first text objective.
- Process-global duplicate memory was removed in favor of per-export IDs; independent DB snapshots may reuse session IDs. Session IDs are **not** attested owners.
- Reported session total cost is preserved; input/output cost allocation and retry counters are `null` where unavailable. The `total_tokens` arithmetic is only a **sum of recorded categories**, not a claim about billable tokens; `provider_billable_total` remains `null`.
- `:free` model suffix is insufficient to verify billing. Free-provider classification records the evidence basis and sets `provider_billing_verified=false`; an unpriced non-Kilo `:free` name remains `free-suffix-unverified`, not independently free-authorized. An observed nonzero cost denies `is_free_provider`.
- Raw assistant/provider errors are replaced by redacted entries plus aggregate error categories. Credential-shaped strings and sensitive dictionary fields are sanitized recursively; reserved JSON object keys are recursively rejected and nonfinite numbers fail JSONL validation.
- Exceptions are counted by type and opaque session ID (up to 20) without exposing exception messages. Failed or missing explicitly requested sessions do not replace the prior output. Successful JSONL writes use a private 0600 atomic temporary file and rename.

## Evidence obtained on x1-370
- `python3 -m pytest -q tests/test_opencode_trace_safety.py tests/test_export_opencode_session_traces.py tests/test_free_subagent_supervisor.py`: **52 passed**.
- `git diff --check`: clean.
- Read-only export against the live OpenCode 1.18.35 SQLite database, filtering **only** known synthetic canary `ses_edfac7c37ffeM2vtohHDhzFxsv`: **one JSONL record**, no parsing failures, output mode **0600**. Its model was `cohere/north-mini-code:free` and its recorded total cost was zero. This does not independently confirm external provider billing.
- Independent read-only OpenCode `explore` session `ses_edf89d855ffeF07kPEzxwu1k3r` flagged suffix attribution, exception swallowing, nested JSON key checks and malformed cost. Each received focused remediation and negative tests. The review inspected in-progress code, so a new post-commit review is still required.

## Limitations
The exporter assumes the 1.18.35 session/message/part schema shown by x1-370, not universal OpenCode database schema compatibility. Source errors and missing provider evidence must not be treated as successful production traces. No multi-node trace consolidation, signed event custody, allocator authority, physical execution, authenticated renewal or production gateway admission has been demonstrated. Source commit `6e3261cd` contains substantial earlier generated changes; integrate only after exact-head CI, code review and scope reconciliation against current main. The original WIP checkout at `trace-contract` was intentionally left intact and this repair uses its own worktree.

## Independent xwing offline replay — 2026-10-09 about 07:50 EDT
- Copied **only** exporter, free-subagent supervisor, selected tests, fixture files and provider manifest into isolated `/home/scott/agent-sprints/20261009/trace-repair-856bf4cf/` on xwing (no Git or service deployment).
- Initial xwing replay exposed host-dependent model catalogue discovery when tests explicitly requested fixture data, plus xwing's default signed-Git-commit policy. The supervisor now treats a supplied fixture path as authoritative; the live-catalog mock test invokes discovery without a fixture. The isolated test process disabled GPG signing using an environment-only `GIT_CONFIG_COUNT` override; no global Git configuration was changed.
- Final xwing command: `python3 -m pytest -q tests/test_opencode_trace_safety.py tests/test_export_opencode_session_traces.py tests/test_free_subagent_supervisor.py --tb=short` — **52 passed in 2.80s**.
- Final x1-370 results for the same suite: **52 passed**. These are offline acceptance tests, not exact-head hosted CI, live provider-billing proof, production dispatch authority, or an iOS device build.
