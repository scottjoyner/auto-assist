# Trace Live Watch Observations — 2026-10-09

**Result:** bounded live-watch core PASS in synthetic tests; production activation NO-GO.

## Implemented

`scripts/live_trace_watch.py` adds a metadata-only tail/catch-up client for the
PR #177 timeline contract. It:

- bootstraps from exactly one newest page by default;
- emits no historical events unless `--emit-existing` is explicit;
- follows keyset pages only while catching up toward already-seen history;
- caps each poll with `--max-pages-per-poll`;
- emits `gap_possible=true` if the budget is exhausted before known history;
- suppresses duplicate event IDs;
- allowlists event metadata and drops payload-shaped fields defensively;
- optionally persists only correlation ID, timestamps and bounded seen event IDs;
- emits health records with lag/staleness and explicit non-attestation fields.
## Test evidence

Command:

`python3 -m pytest -q tests/test_live_trace_watch.py tests/test_trace_detail_pages.py`

Result: **35 passed** with one existing Starlette TestClient deprecation warning.

Additional checks:

- `python3 -m py_compile scripts/live_trace_watch.py tests/test_live_trace_watch.py` — PASS
- `python3 scripts/live_trace_watch.py --help` — PASS
- `git diff --check` — PASS

The new synthetic tests cover bounded bootstrap, multi-page burst recovery,
page-budget gap signaling, duplicate suppression, payload-field stripping,
explicit bounded historical emission, and metadata-only state persistence.

## Important negative result / limitation

The first prototype included client-side credential/header handling. That path was
removed before publication. The current script performs no credential handling and
therefore is **not a production-authenticated operator client** for PR #177 yet.
No attempt was made to weaken or bypass the server's authentication dependency.
Authentication/session integration should be implemented through the existing
trusted operator surface rather than a new secret-bearing CLI convention.

## Coexistence evidence

No live inference benchmark, Neo4j mutation, trace event write, service restart,
feature-flag change, NAS operation, provider call, or production trace read was
performed in this slice. Work ran in an isolated worktree stacked on PR #177.

## Next gate

The preferred next integration is an authenticated same-origin operator surface
(browser or an existing trusted client) that reuses this bounded catch-up logic.
It must retain the PR #177 feature gate, never silently fall back to the legacy
full-detail endpoint, and preserve the physical read-concurrency/cancellation and
trusted-ingress blockers.

This result improves the diagnostic primitive; it does not prove trace custody,
harvester completeness, physical producer identity, or production readiness.