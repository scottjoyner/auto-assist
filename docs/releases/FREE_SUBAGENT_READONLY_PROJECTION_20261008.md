# Free subagent session/trace projection — preregistration, 2026-10-08

Source baseline: auto-assist main a300072d11787a58a4587fa325f1b8fd66803730.

**Baseline claim and countercheck:** Existing test passed on the developer's
real OpenCode database, but both targeted tests failed with a clean empty HOME
as on ephemeral GitHub Actions. The production code uses readonly SQLite with
query_only; its test depended on ambient ~/.local/share/opencode/opencode.db.
Trace exporter fields are silently omitted when an optional source database
does not exist, making "source missing" indistinguishable from "not requested".

**Prediction:** With HOME redirected to a temporary SQLite fixture containing
one recent session, the real discover_live_sessions(query_only=True) returns
that session and a direct SQLite readonly connection cannot CREATE even a temp
table. Under missing HOME database, discovery remains fail-closed with explicit
unavailable handling; it never opens SQLite in read-write/create mode.

When a trace exporter was explicitly provided, projection must always expose
its typed presence fields. Missing DB produces status "source_unavailable",
count=None, empty sample and summaries (not fabricated "zero activity").
A source that exists and successfully exports zero rows has status "ok"
and count=0. A failing exporter has status "error" and a sanitized error
string, never an "ok" status or false zero count. No direct free provider
calls, token admission/dispatch, route or quota changes are allowed.

**Acceptance:** Run the focused suite in a clean HOME and in a normal HOME;
no dependency on the owner's current OpenCode DB; follow with the whole
free_subagent_supervisor suite. Test artifacts remain in tmp_path only.

## Observed local results

- In a normal operator HOME the old two tests passed, concealing their
  dependence on an existing OpenCode database. Under an empty HOME the
  **unchanged baseline failed 2/2**, reproducing CI's missing-source behavior.
- After the fix, **34/34 focused tests pass** in both normal and empty HOME
  (three direct trace tests cover absent/empty/failed sources).
- No token inference call, session mutation, router policy, database creation
  by production discovery, task dispatch, quota grant, or production-service
  change occurred. The synthetic SQLite fixtures were created in tmp_path,
  not in the operator's HOME.
- The read-only projection now distinguishes trace_exporter_status:
  source_unavailable (unknown count), ok (measured count, including 0),
  error (unknown count). Error details are type-only, not internal paths or
  potentially private backend strings.
- The next acceptance gate is the same isolated tests on GitHub CI, then
  read-only integration into the combined RC without changing any authority.
