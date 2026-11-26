# AssistX Iteration Plan — Round 2 (2026-11-26)

**Status:** planning document only. No runtime changes, no DB writes, no schema
migration, no service/unit restart, no compose change, no push and no PR are
made or proposed for execution by this document.

**Worktree / branch:** `/home/scott/git/wt-assistx-plan-r2-20261126` on
`agent/assistx-iteration-plan-r2-20261126`, branched from the current HEAD of
`/home/scott/git/assistx-iterate-20261118` (`iterate/20261118`) at
`b0b4e4485e4b5432c49045142d46658d0326f6ef`.

**Verification basis:** every hash below was read back with
`git log main..HEAD --oneline` in this worktree. The result is **17 commits,
0 behind `main`**. No hash in this document is inferred from a report.

**Source context:** `session4-autoingest-assistx-20261126.md` §2 and §3(a)(4–6),
`FLEET-ITERATION-STATUS-20261126.md` §"AssistX" and §"Remaining ranked backlog",
report 114 §7, and report 86 §5.

---

## 0. Standing boundaries

- INCIDENT-173 P0 hold remains in force. No credential, env snapshot or
  `.git-credentials` content was read, printed or stored while producing this
  plan; none appears in this document.
- No `/nas` traversal, no broad filesystem scan, no live DB write, no WAL
  checkpoint, no unit/service restart, no compose edit.
- Work was confined to one new worktree; today's AssistX cutover-successor
  worktree and the mobile/worker lanes were not touched.
- The two "physical" or otherwise live-state experiments described below are
  explicitly marked **do not run** under this plan; they need owner
  authorization and a disposable, independently admitted environment.

---

## 1. Backlog split at a glance

| Plan | Theme | Commits | Diff surface (from `git diff --stat main...HEAD`) |
|---|---|---:|---|
| A | Outbox correctness / fencing / health | 6 | `src/assistx/outbox_client.py`, `src/assistx/runtime.py`, 5 outbox test modules, `tests/test_runtime_contract.py` |
| B | Fleet hardware evidence + route-auth CI | 11 | two new `fleet_hardware_*` modules, `api_router.py`, templates/static assets, receipt tests + physical harness, two CI workflows, two docs |

Review order within each plan is **oldest → newest** (git-log order reversed),
so that each commit builds on the invariant established by the previous one.
Plan A and Plan B are separable: they share only `src/assistx/api_router.py`
(Plan B) and `src/assistx/runtime.py` (Plan A), with no overlapping hunk, so
either plan may be reviewed first.

---

## 2. Plan A — Outbox correctness / fencing / health (6 commits)

### 2.1 Review order (verified hashes)

| # | Hash | Subject | Key files |
|---:|---|---|---|
| A1 | `6ef1273` | `fix(outbox): bound the queue — no phantom retries, terminal exhaustion` | `src/assistx/outbox_client.py`, `tests/test_outbox_client.py` |
| A2 | `3207047` | `fix(outbox): claim rows before delivery` | `src/assistx/outbox_client.py`, `tests/test_outbox_claim.py` |
| A3 | `f3478e7` | `feat(health): expose outbox depth and staleness` | `src/assistx/runtime.py`, `tests/test_outbox_health.py`, `tests/test_runtime_contract.py` |
| A4 | `68d108f` | `test(outbox): cover legacy-schema migration and claim exclusivity` | `tests/test_outbox_client.py` |
| A5 | `9e6d26f` | `fix(outbox): fence terminal release and serialize cross-client event deduplication` | `src/assistx/outbox_client.py`, `tests/test_outbox_fencing_native.py` |
| A6 | `b0b4e44` | `fix(outbox): bound and validate acknowledgements and serialize schema migration` | `src/assistx/outbox_client.py`, `tests/test_outbox_ack_migration_native.py` |

`b0b4e44` is the current HEAD of both the iterate checkout and this branch.

### 2.2 Exact test commands (from this repo's own suite)

Focused, offline, real-SQLite regressions (all use `tempfile` databases — none
touches `/home/scott/.assistx_outbox.db`):

```
python -m pytest -q tests/test_outbox_client.py tests/test_outbox_claim.py \
  tests/test_outbox_ack_migration_native.py tests/test_outbox_fencing_native.py \
  tests/test_outbox_health.py tests/test_runtime_contract.py
```

Canonical suite commands as defined by the repository itself:

```
make test                                   # Makefile: pytest -q
make smoke                                  # Makefile: python -m compileall src && pytest -q
pytest -q -m "not integration" --ignore=tests/integration    # .github/workflows/ci.yml
```

The CI job supplies `BASIC_AUTH_*` via its own environment; the values are
intentionally not reproduced in this document. Preference for Plan A review is
the focused command above, followed by the CI command before any PR decision.

### 2.3 Review risks

- **A1 queue bounding:** verify the demotion-to-`dead` cap cannot reclassify a
  still-deliverable row, and that the "no phantom retries" change does not drop
  a row that is merely inside backoff. Check the cap is applied inside the same
  transaction as enqueue.
- **A2 claim-before-delivery (largest change, ~362 lines):** this is the highest
  risk commit in Plan A. Confirm lease ownership (`claim_owner` / `claimed_at`)
  is checked on *every* release path, that a reclaimed lease cannot resurrect a
  delivered row, and that the dedupe `event_id` lookup spans the right status
  set without creating a uniqueness race.
- **A3 health/readiness:** `/health` starts returning 503 for a stale queue by
  default. Confirm `ASSISTX_OUTBOX_READINESS=0` is understood as a temporary
  drain escape hatch, and that the probe opens SQLite `mode=ro` so it can never
  create/migrate/purge a live queue (`test_probe_does_not_write_to_the_database`
  asserts byte-identical DB bytes).
- **A4 migration coverage:** test-only commit; verify it covers the legacy
  schema path (pre `claim_owner`/`claimed_at` columns) and concurrent claim
  exclusivity, rather than only the happy path.
- **A5 fencing:** the native test manually backdates `claimed_at`; confirm the
  production lease comparison uses the same clock semantics (UTC ISO strings)
  and that cross-client dedupe serialization holds under a real `unittest`
  concurrency run.
- **A6 acks + migration serialization:** confirm a non-JSON / non-boolean
  `accepted` field is never treated as a successful ack, and that startup
  migration is serialized (no two workers racing `ALTER TABLE`).
- **Cross-plan risk:** the live outbox is **not** in the state these tests
  assume (see §5), so a green suite is *not* evidence the live queue is healthy.
  No reconciliation is authorized by this document.

---

## 3. Plan B — Fleet hardware evidence + route-auth CI (11 commits)

### 3.1 Review order (verified hashes)

| # | Hash | Subject | Key files |
|---:|---|---|---|
| B1 | `4583e1d` | `feat(fleet): expose authenticated read-only hardware preview from private inventory` | `src/assistx/fleet_hardware_preview.py`, `src/assistx/api_router.py`, `tests/test_fleet_hardware_preview.py`, `docs/FLEET_HARDWARE_PREVIEW.md` |
| B2 | `c0f565e` | `ci(fleet): isolate hardware preview contract from legacy suite failures` | `.github/workflows/fleet-hardware-preview.yml`, `docs/FLEET_HARDWARE_PREVIEW.md` |
| B3 | `272fad6` | `security(fleet): forbid shared caching of authenticated hardware preview` | `src/assistx/fleet_hardware_preview.py`, `tests/test_fleet_hardware_preview.py`, `docs/FLEET_HARDWARE_PREVIEW.md` |
| B4 | `7a19cff` | `feat(fleet): add authenticated hardware evidence UI and explicit trace provenance viewer` | `src/assistx/api_router.py`, `src/assistx/fleet_hardware_preview.py`, `templates/fleet_hardware_evidence.html`, `templates/base.html`, `templates/fleet_dashboard.html`, `templates/traces.html`, `static/js/fleet_hardware_preview.js`, `static/js/fleet_trace_evidence.js`, `static/js/traces.js`, `static/css/fleet_hardware_evidence.css`, `tests/ui/fleet_hardware_evidence.test.cjs`, `.github/workflows/fleet-hardware-preview.yml` |
| B5 | `31510f9` | `security(fleet): reconcile pinned no-follow private reads and finite-capacity guards` | `src/assistx/fleet_hardware_preview.py`, `tests/test_fleet_hardware_preview.py`, `docs/FLEET_HARDWARE_PREVIEW.md` |
| B6 | `901cafc` | `ci(fleet): verify complete AssistX app route auth and standalone operator page` | `tests/test_fleet_hardware_route_smoke.py`, `.github/workflows/fleet-hardware-preview.yml`, `docs/FLEET_HARDWARE_PREVIEW.md` |
| B7 | `2b0c867` | `feat(traces): add opt-in immutable post-hoc fleet hardware inspection receipts` | `src/assistx/fleet_hardware_inspection.py`, `src/assistx/api_router.py`, `tests/test_fleet_hardware_inspection.py`, `tests/test_fleet_hardware_route_smoke.py`, `tests/ui/fleet_hardware_evidence.test.cjs`, `docs/FLEET_HARDWARE_INSPECTION_RECEIPTS.md`, `.github/workflows/fleet-hardware-preview.yml` |
| B8 | `ddf9cef` | `security(traces): deny hardware receipt writes without unique DB constraint` | `src/assistx/fleet_hardware_inspection.py`, `tests/test_fleet_hardware_inspection.py`, `docs/FLEET_HARDWARE_INSPECTION_RECEIPTS.md` |
| B9 | `2bdf71b` | `test(traces): serialize receipt quota and prove physical Neo4j concurrency` | `tests/physical/fleet_receipt_disposable_neo4j.py`, `tests/test_fleet_hardware_inspection.py`, `tests/test_fleet_receipt_lab_guard.py`, `src/assistx/fleet_hardware_inspection.py`, `.github/workflows/fleet-hardware-preview.yml`, `docs/FLEET_HARDWARE_INSPECTION_RECEIPTS.md` |
| B10 | `07a30fb` | `security(traces): require explicit receipt writer ACL and no-store audit responses` | `src/assistx/fleet_hardware_inspection.py`, `tests/test_fleet_hardware_inspection.py`, `docs/FLEET_HARDWARE_INSPECTION_RECEIPTS.md` |
| B11 | `9bc6e63` | `security(traces): independently authenticate receipt writes across trusted headers` | `src/assistx/fleet_hardware_inspection.py`, `tests/test_fleet_hardware_inspection.py`, `docs/FLEET_HARDWARE_INSPECTION_RECEIPTS.md` |

### 3.2 Exact test commands (verbatim from `.github/workflows/fleet-hardware-preview.yml`)

Environment used by that job: `PYTHONPATH=src`,
`ASSISTX_RUNTIME_PROFILE=test`, `ASSISTX_DEPENDENCY_MODE=compat`, and
**deliberately no** `ASSISTX_FLEET_HARDWARE_ROOT` and no private fleet inventory.

```
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m py_compile src/assistx/fleet_hardware_preview.py src/assistx/fleet_hardware_inspection.py
ruff check src/assistx/fleet_hardware_preview.py src/assistx/fleet_hardware_inspection.py src/assistx/api_router.py tests/test_fleet_hardware_preview.py tests/test_fleet_hardware_inspection.py tests/test_fleet_receipt_lab_guard.py tests/test_fleet_hardware_route_smoke.py
python -m pytest -q tests/test_fleet_hardware_preview.py tests/test_fleet_hardware_inspection.py tests/test_fleet_receipt_lab_guard.py
node --check static/js/fleet_hardware_preview.js
node --check static/js/fleet_trace_evidence.js
node --check static/js/traces.js
node --test tests/ui/fleet_hardware_evidence.test.cjs
python -m pytest -q tests/test_fleet_hardware_route_smoke.py
```

For a whole-repo pre-PR check, the repository's canonical command remains:

```
pytest -q -m "not integration" --ignore=tests/integration
```

### 3.3 Review risks

- **B1/B4/B7 private-inventory exposure:** confirm every read path is gated by
  `auth`, returns only the synthetic/sanitized preview shape, and never echoes
  private paths or raw inventory into an unauthenticated response. The UI
  commits touch `templates/base.html` and `templates/traces.html`, so verify
  no script is added to a public page without the same auth fence.
- **B2 CI isolation:** the dedicated workflow is intentionally scoped to the
  preview contract so legacy-suite failures do not mask it. Confirm path filters
  cover every file B1–B11 touches, or a later commit could land untested.
- **B3 no shared cache:** verify no `Cache-Control: public`, shared proxy cache,
  or process-level memoization can serve one user's authenticated preview to
  another; the test asserts the negative but the middleware order matters.
- **B5 no-follow + finite capacity:** re-check symlink resolution (no-follow on
  private reads) and that every bounded read has an actual capacity limit,
  including error paths that partially consume a file.
- **B6 route-auth completeness:** `test_fleet_hardware_route_smoke.py` uses
  `pytest.importorskip("langgraph")`; in an environment without `langgraph`
  these checks are **skipped, not failed**. A reviewer must confirm they actually
  ran, or force an environment where the dependency is present.
- **B7–B11 receipt writer chain:** the security model is cumulative —
  unique-DB constraint (B8), explicit writer ACL + no-store (B10), and
  independent auth across trusted headers (B11) all close distinct holes. Do
  not review the final commit alone; re-run the full inspection test module at
  B11. Receipt writes must remain opt-in and immutable.
- **B9 physical harness — DO NOT RUN under this plan.**
  `tests/physical/fleet_receipt_disposable_neo4j.py` requires
  `ASSISTX_RECEIPT_DISPOSABLE_LAB=I_ACCEPT_DISPOSABLE_ONLY` plus a
  `NEO4J_RECEIPT_LAB_URI` on a random non-default localhost port and an empty
  fresh graph; it is designed for a disposable container that is then
  destroyed. It must never point at production Neo4j. The safe, no-Docker
  guard-rail coverage is `tests/test_fleet_receipt_lab_guard.py`, which the CI
  job does run.
- **No deployment:** none of these commits installs a binary, edits systemd
  units, restarts a service, or changes compose definitions.

---

## 4. DB-derived outbox health signal (specification, not implementation)

### 4.1 Existing foundation

Commit **`f3478e7` — `feat(health): expose outbox depth and staleness`**
(full hash `f3478e7b96c2f8115c63f340a4e418e1a55121b8`) already introduced
`read_queue_health()` in `src/assistx/outbox_client.py` and the
`outbox` section of `build_runtime_health()` in `src/assistx/runtime.py`, with
coverage in **`tests/test_outbox_health.py`** (plus
`tests/test_runtime_contract.py`). That implementation:

- opens the SQLite file **read-only** (`sqlite3.connect("file:{path}?mode=ro",
  uri=True, timeout=5)`), and its `read_only=True` default is what lets a probe
  run against a live queue without creating, migrating or purging it;
- derives counts from the database itself with
  `SELECT status, COUNT(*) FROM outbox GROUP BY status`, and staleness from
  `SELECT MIN(created_at) FROM outbox WHERE status IN ('pending','failed','claimed')`;
- has an explicit byte-for-byte assertion that the probe does not write
  (`test_probe_does_not_write_to_the_database`), plus terminal-row exclusion
  from the age metric (`test_health_ignores_terminal_rows_when_measuring_age`),
  stale-readiness gating with `ASSISTX_OUTBOX_STALE_S` (default 24 h), and the
  `ASSISTX_OUTBOX_READINESS=0` drain override.

### 4.2 The signal this plan specifies

Replace reliance on the **restart-reset in-memory counter** (the router's
`pending=0`, which is an unreliable post-reboot value) with a value derived on
each probe from the durable table:

```sql
SELECT status, attempt_count FROM outbox;
```

Bucket the rows in Python (no new column, no index, no schema change):

| Signal | Derivation from `(status, attempt_count)` |
|---|---|
| `pending` | `status = 'pending' AND attempt_count = 0` — never attempted |
| `retry` | `status IN ('pending','failed') AND attempt_count > 0` — deliverable, previously attempted, still inside the retry budget |
| `dead` | `status = 'dead' OR attempt_count >= max_retries` — terminal exhaustion (matches the existing terminal-exhaustion rule in `outbox_client.py`) |
| `claimed` / `delivered` | `status = 'claimed'` / `status = 'delivered'` — retained for completeness |
| `oldest_pending_age_s` | unchanged: `MIN(created_at)` over the deliverable statuses above, age vs `ASSISTX_OUTBOX_STALE_S` |

The probe should keep the `read_only=True` connection and the existing
read-only contract, and the assertions in `tests/test_outbox_health.py` should
be extended to cover the three-way split — e.g. seed rows at `attempt_count`
0, 3 and 9 and assert `pending`, `retry` and `dead` are reported from the DB,
not from any in-memory counter that was reset by a restart.

**Why attempt-9 matters:** the current durable table has 48 rows, all
`pending`, of which **14 sit at `attempt_count = 9`**. A status-only counter
reports "48 pending" and looks homogeneous; the `attempt_count` dimension is
what exposes those 14 as effectively wedged retry candidates. See §5.

### 4.3 Explicit non-goals

**This document proposes no DB write and no schema migration.** It adds no
column, no index and no trigger to `outbox`; it issues no `UPDATE`, `DELETE`,
`INSERT` or schema change against any live database. It does **not** authorize
outbox reconciliation, quarantine of the 14 attempt-9 rows, or repair of the
live queue — those are owner-gated and require a byte-for-byte backup, writer
identification and a reversible sidecar. The only code change contemplated here
is a read-only refinement of the existing `f3478e7` probe and its tests, to be
reviewed as part of Plan A (A3).

---

## 5. Stage-4 failure-code decision brief (doc-only)

**Already deployed and verified (stages 1–3, report 114):** the closed-enum key
`assistx_window_failed_by_code` is present in the private index's `meta`,
currently `{"zero_duration":5}`; its values sum to `assistx_window_failed`;
publisher and indexer md5s match; nine `assistx_*` meta keys are present; the
other eight keys are byte-identical; no correlation identifiers or raw reason
text leaked into the index; no service was restarted.

**What remains — and why it is owner-gated:**

1. **Viewer key allowlist.** The compiled viewer's coverage payload is built
   from a hardcoded meta-key `IN (...)` list in `src/analytics.rs` of the
   trace-viewer source (worktree
   `/home/scott/git/trace-viewer-assistx-failure-code-20261124`). That list does
   not yet include `assistx_window_failed_by_code`, so the key is correctly in
   the private index but absent from the dashboard `coverage` set (49 keys
   before, 49 after — an expected, documented negative result from report
   86 §5 and report 114 §7). Adding the key is a source change in a *different*
   repository, outside this worktree's mandate.
2. **Rust binary rebuild.** The dashboard serves
   `/home/scott/.local/libexec/trace-viewer`, unchanged at md5
   `317134b446d2b2f9a47f90594bd65a5a` (mtime 2026-10-30). Surfacing the key
   requires a fresh Rust build and binary swap, which is a separate
   authorization (report 114 §8: no Rust rebuild or binary swap performed).

**This document proposes neither the allowlist edit nor the rebuild, and no
service/unit restart.** Both need owner sign-off and a maintenance window; the
decision is *defer*, with the data left correct and waiting in the index.

---

## 6. Still-open facts (read-only verified for this plan)

These were re-verified read-only while preparing this document; no value was
modified and no credential was accessed.

| Fact | Verified value | Source / method |
|---|---|---|
| Iterate backlog | 17 commits ahead, 0 behind `main`; HEAD `b0b4e448...` | `git log main..HEAD --oneline`, `git rev-list --left-right --count main...HEAD` in this worktree |
| Outbox rows | **48 total, all `status='pending'`** | read-only SQLite `file:/home/scott/.assistx_outbox.db?mode=ro&immutable=1`: `SELECT status, COUNT(*) FROM outbox GROUP BY status` |
| Attempt distribution | **28 at attempt-0, 6 at attempt-3, 14 at attempt-9** | same read-only query: `SELECT attempt_count, COUNT(*) FROM outbox GROUP BY attempt_count` |
| Main AssistX checkout | clean, **0 / 0** vs `origin/main` | `git status --porcelain` (empty) and `git rev-list --left-right --count HEAD...origin/main` |
| Missing compose files | `/home/scott/git/auto-assist-wt-ab3bb64/docker-compose.yml` and `.../compose.override.yml` both **absent** | exact-path `stat` only; no directory traversal |
| Stage-4 viewer | compiled binary unchanged; key absent from dashboard `coverage` | report 114 §7–§8; source allowlist in `src/analytics.rs` |

**Interpretation:** the 48 / 14 finding still holds exactly. The rows are
unclaimed and stale, and the router's `pending=0` remains an in-memory,
post-reboot-unreliable value (§4). None of this is repaired here; all live-state
remediation remains owner-gated under INCIDENT-173.

---

## 7. Bottom line

- Two reviewable PR plans are ready as **documents only**: Plan A (6 outbox
  commits, `6ef1273` → `b0b4e44`) and Plan B (11 fleet-evidence/route-auth
  commits, `4583e1d` → `9bc6e63`). No PR is opened.
- The outbox-health refinement builds on existing commit `f3478e7` and
  `tests/test_outbox_health.py` and derives `pending`/`retry`/`dead` from
  `SELECT status, attempt_count FROM outbox`; **no DB write or schema migration
  is proposed**.
- Stage 4 (viewer key allowlist + Rust rebuild) remains owner-gated; no rebuild
  or restart is proposed.
- No push, PR, merge, live DB write, compose change or service restart was
  performed.
