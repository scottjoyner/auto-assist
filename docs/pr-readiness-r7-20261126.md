# Paste-ready PR descriptions — pushed AssistX PR-ready branches (round 7, 2026-11-26)

Prepared by Session R7-4. This branch (`agent/pr-readiness-r7-20261126`) is based
on `origin/main` `a300072d11787a58a4587fa325f1b8fd66803730`; it carries only the
two documents/tests described below. **No PR was opened and no push/merge to
`main` is authorized** (INCIDENT-173 P0 hold; owner gate). Hashes and test
results were verified from local `git log`/`git diff` and from
`/home/scott/opencode-reviews/round6-session4-assistx-20261126.md` and the status
doc (rounds 1, 2, 3, 4, 6); results quoted from those reports are labeled as such.

Repo: AssistX / auto-assist (git dir `/home/scott/assistx-fleet-evidence/.git`).
The two branches below are independent siblings, both off `origin/main` `a300072`.

---

## 1. Outbox correctness / fencing / health branch

**Branch:** `agent/outbox-pr-ready-r6-20261126`
**HEAD:** `83f7083023d5c9bd308088bfac915ceca06a686d`
**Base:** `origin/main` `a300072d11787a58a4587fa325f1b8fd66803730`
**Diff vs base:** 8 files, +1028/−47 (`src/assistx/outbox_client.py` +402,
`src/assistx/runtime.py` +39, 5 new outbox test modules, `tests/test_outbox_client.py`
+118, `tests/test_runtime_contract.py` +8).
**Commits (6, oldest → newest, all clean cherry-picks from the iterate branch):**

| New hash | Subject |
|---|---|
| `bb53997` | fix(outbox): bound the queue — no phantom retries, terminal exhaustion |
| `4b49166` | fix(outbox): claim rows before delivery |
| `907d4e7` | feat(health): expose outbox depth and staleness |
| `ceb6b5b` | test(outbox): cover legacy-schema migration and claim exclusivity |
| `e45e94f` | fix(outbox): fence terminal release and serialize cross-client event deduplication |
| `83f7083` | fix(outbox): bound and validate acknowledgements and serialize schema migration |

### Paste-ready title

```
fix(outbox): fence claims/deduplication, bound retries and expose read-only queue health
```

### Paste-ready body

```
## What changed

Six self-contained commits, split from the 17-commit iterate backlog (the
hardware-evidence half is a separate PR):

- Rows are claimed before delivery; the bounded queue no longer produces
  phantom retries and terminal exhaustion is explicit.
- Terminal release is fenced and cross-client event deduplication is
  serialized; acknowledgements are bounded/validated and schema migration is
  serialized.
- New `read_queue_health()` probe reports depth, status counts and oldest
  deliverable age from a **read-only** SQLite connection (`mode=ro`), with a
  default 24 h staleness threshold and an operator opt-out
  (`ASSISTX_OUTBOX_READINESS=0`) that does not hide the numbers.
- `runtime._check_outbox()` wires the probe into `build_runtime_health()` and
  therefore `/health`; readiness uses only `ok`/`readiness_enforced`.
- Legacy-schema migration and claim-exclusivity coverage added; all 8 touched
  files were verified byte-for-byte identical to the iterate originals.

## Why

The live outbox had 48 pending rows (28 at attempt-0, 6 at attempt-3, 14 at
attempt-9), all unclaimed; the attempt-9 rows are the known wedged backlog.
The restart-reset in-memory counter could not show this. These commits make
claiming/delivery safe under concurrency and make queue health observable
without ever writing the live DB. The round-4 quarantine of the 14 attempt-9
rows is a separate, reversible operator action and is NOT part of this PR.

## Tests / validation (recorded in round 6)

- `python -m pytest -q tests/test_outbox_claim.py tests/test_outbox_fencing_native.py tests/test_outbox_health.py tests/test_outbox_client.py`
  → **27 passed, 1 warning** (pre-existing Starlette `python_multipart`
  PendingDeprecationWarning), RC 0.
- `python -m pytest -q tests/test_outbox_client.py tests/test_outbox_claim.py tests/test_outbox_ack_migration_native.py tests/test_outbox_fencing_native.py tests/test_outbox_health.py tests/test_runtime_contract.py`
  → **38 passed, 1 warning**, RC 0.
- All tests use pytest `tmp_path` / `tempfile` databases; the live
  `/home/scott/.assistx_outbox.db` was never opened for read or write.
- Round 3's full CI command on the iterate branch
  (`python -m pytest -q -m "not integration" --ignore=tests/integration`)
  showed **80 failed / 763 passed** vs an exact-HEAD baseline of
  **80 failed / 758 passed**: zero new failures, +5 passes. The 80 failures are
  pre-existing and outside outbox modules.

## Risks / notes

- `ok=False` is driven by age (>24 h), not depth; an operator can keep `/health`
  green while draining a known backlog, but the payload still reports it.
- The probe is read-only and treats a missing/unreadable DB as non-fatal; it
  cannot repair a wedged queue by itself.
- Schema migration is serialized, not eliminated: a hostile/legacy schema still
  needs the provided tests and an owner decision before a live migration.
- The later additive keys `retry` / `attempt_buckets` live on the separate
  branch `agent/outbox-health-db-derived-r3-20261126` (`b43d353`) and are NOT in
  this PR. A consumer-shape compatibility test for them is included on the
  documentation branch `agent/pr-readiness-r7-20261126` (see §3 below).
- No live DB, WAL, cron, service or network operation was performed here.

## Authorization

**No push or merge to `main` is authorized.** This text is a paste-ready draft;
no PR was opened by this session.
```

---

## 2. Fleet hardware evidence + route-auth branch

**Branch:** `agent/hardware-evidence-pr-ready-r6-20261126`
**HEAD:** `f44974cbc4de575881597b6cef98805c9540a5cc`
**Base:** `origin/main` `a300072d11787a58a4587fa325f1b8fd66803730`
**Diff vs base:** 20 files, +2731/−4 (workflow, docs, two `fleet_hardware_*`
modules, `api_router.py`, templates/static assets, tests, physical harness file).
**Commits (11, oldest → newest, all clean cherry-picks):**

| New hash | Subject |
|---|---|
| `f9eca4e` | feat(fleet): expose authenticated read-only hardware preview from private inventory |
| `b789bb8` | ci(fleet): isolate hardware preview contract from legacy suite failures |
| `818db44` | security(fleet): forbid shared caching of authenticated hardware preview |
| `7f8d5b8` | feat(fleet): add authenticated hardware evidence UI and explicit trace provenance viewer |
| `9c5b206` | security(fleet): reconcile pinned no-follow private reads and finite-capacity guards |
| `8353e6b` | ci(fleet): verify complete AssistX app route auth and standalone operator page |
| `2f15210` | feat(traces): add opt-in immutable post-hoc fleet hardware inspection receipts |
| `8d8ede1` | security(traces): deny hardware receipt writes without unique DB constraint |
| `3315a36` | test(traces): serialize receipt quota and prove physical Neo4j concurrency |
| `cbfee1e` | security(traces): require explicit receipt writer ACL and no-store audit responses |
| `f44974c` | security(traces): independently authenticate receipt writes across trusted headers |

### Paste-ready title

```
feat(fleet): authenticated hardware evidence preview and independently authenticated receipts
```

### Paste-ready body

```
## What changed

The 11-commit hardware half of the 17-commit iterate backlog, kept separate
from the outbox PR:

- Authenticated, read-only hardware preview derived from a private fleet
  inventory, with pinned no-follow reads, finite-capacity guards, and an
  explicit no-shared-cache policy.
- Authenticated hardware-evidence UI plus an explicit trace-provenance viewer,
  and standalone operator page / full app route-auth CI verification.
- Opt-in immutable post-hoc fleet hardware inspection receipts; writes require
  an explicit receipt-writer ACL, independent authentication across trusted
  headers, a unique DB constraint, and no-store audit responses.
- CI isolates the hardware-preview contract from unrelated legacy-suite
  failures; a physical concurrency harness file is included in the branch.
- All 20 touched files were verified byte-for-byte identical to the iterate
  originals; zero cherry-pick conflicts and zero skipped commits.

## Why

Hardware evidence comes from a private inventory and must not leak through
caches or trusted-header spoofing. Receipts need independent authentication and
a DB uniqueness constraint to be meaningful audit evidence. Separating these
11 commits from the outbox fixes keeps each reviewable on its own.

## Tests / validation (recorded in round 6)

Workflow-equivalent env: `PYTHONPATH=src`, `ASSISTX_RUNTIME_PROFILE=test`,
`ASSISTX_DEPENDENCY_MODE=compat`, no `ASSISTX_FLEET_HARDWARE_ROOT` and no
private inventory.

- `python -m py_compile src/assistx/fleet_hardware_preview.py src/assistx/fleet_hardware_inspection.py` → PASS.
- `ruff check` over the two modules, `api_router.py` and the four related test
  files → **All checks passed!** (zero new findings).
- `python -m pytest -q tests/test_fleet_hardware_preview.py tests/test_fleet_hardware_inspection.py tests/test_fleet_receipt_lab_guard.py`
  → **57 passed, 1 warning** (pre-existing Starlette deprecation), RC 0.
- `node --check` on `static/js/fleet_hardware_preview.js`,
  `static/js/fleet_trace_evidence.js`, `static/js/traces.js` → all OK.
- `node --test tests/ui/fleet_hardware_evidence.test.cjs` → **8/8 passed**.
- `python -m pytest -q tests/test_fleet_hardware_route_smoke.py` → **4 passed,
  1 warning**; these executed rather than skipping on
  `pytest.importorskip("langgraph")`, so the route-auth checks really ran.

**Skipped:** the workflow's `pip install -r requirements.txt -r requirements-dev.txt`
(dependencies already installed; no network/install step) and, explicitly, the
**physical Neo4j harness** `tests/physical/fleet_receipt_disposable_neo4j.py`.
It is owner-gated and usable only with a disposable container; it was NOT run,
so physical concurrency is not claimed as verified here. Its safe no-Docker
guard-rail coverage (`tests/test_fleet_receipt_lab_guard.py`) was run in the
57-test command above.

## Risks / notes

- The DB unique constraint and receipt-writer ACL are prerequisites; enabling
  receipts against a schema without them is deliberately denied.
- Private inventory paths are opt-in and never bundled; missing configuration
  must fail closed, not fall back to a public source.
- New UI assets change the authenticated surface; the route-auth smoke tests
  are the guard against regressions.
- No service/unit restart, binary rebuild, compose change or network call.

## Authorization

**No push or merge to `main` is authorized.** This text is a paste-ready draft;
no PR was opened by this session.
```

---

## 3. Local code-safety check on this documentation branch

**Files added here:** `docs/pr-readiness-r7-20261126.md` (this file) and
`tests/test_outbox_health_consumer_compat_r7.py` (new, in-memory fixture only).

**Consumer identified from code (no fabricated consumer):**

1. On this branch (`origin/main` `a300072`), `src/assistx/api.py::health()`
   (around line 1547) calls `build_runtime_health()` and returns
   `JSONResponse(status_code=200 if payload.get("ok") else 503, content=payload)`.
   It has no strict schema and does not enumerate outbox keys.
2. On the pushed outbox branch's local worktree
   (`/home/scott/git/wt-assistx-outbox-pr-r6-20261126`), `src/assistx/runtime.py::_check_outbox()`
   imports `read_queue_health` at call time, adds `readiness_enforced`, and
   `build_runtime_health()` exposes the dict as `payload["outbox"]`; readiness
   reads only `outbox.get("ok")` / `outbox.get("readiness_enforced")`. The same
   `/health` response passes it through. No other local consumer was found by a
   bounded grep of `src/`/`tests/` for `read_queue_health`, `["outbox"]` and
   `attempt_buckets` — there is no strict/Pydantic response model to break.

**What the new test does:** builds a legacy-shaped health payload from an
in-memory SQLite `outbox` fixture (4 synthetic rows), adds the producer's two
additive keys (`retry`, `attempt_buckets` with `max_retries`) as in `b43d353`,
and feeds it through the real `api.health()` consumer. It asserts every legacy
key keeps its value, the additive keys pass through, and the 200/503 status is
still driven by the legacy `ok`. No live DB/WAL, no network, no file write.

**Test commands and results actually run in this worktree:**

| Command | Result |
|---|---|
| `python -m pytest -q tests/test_outbox_health_consumer_compat_r7.py tests/test_runtime_contract.py` | **5 passed, 1 warning** (pre-existing Starlette `python_multipart` PendingDeprecationWarning) |
| `python -m ruff format tests/test_outbox_health_consumer_compat_r7.py` | 1 file reformatted (formatting only) |
| `python -m ruff check tests/test_outbox_health_consumer_compat_r7.py` | **All checks passed!** |
| `python -m pytest -q tests/test_outbox_health_consumer_compat_r7.py tests/test_runtime_contract.py tests/test_outbox_client.py` | **9 passed, 1 warning** (same pre-existing warning) |

The full AssistX CI suite was not run (launch hold:
`FLEET_LAUNCH_HOLD RAM_LOW,SWAP_RESERVE_LOW,IO_PRESSURE,MEMORY_PRESSURE`), and
the physical Neo4j harness remains skipped.

## Provenance of quoted results

- All branch tips, bases, commit subjects and diff summaries were read with
  `git log`/`git diff` in `/home/scott/assistx-fleet-evidence` (no fetch/push).
- Round-6 report `round6-session4-assistx-20261126.md` records the 27/38-test
  Plan A results, the 57/8/4 Plan B results, ruff/py_compile/node checks and
  the explicit physical-Neo4j-harness skip.
- Round-3 report `round3-session5-assistx-20261126.md` records the `b43d353`
  producer shape, the 29-test focused set and the 80-failure CI baseline.
- Status doc round 6 records both PR-ready tips (`83f7083`, `f44974c`) and the
  `a300072` base; round 4 records the reversible 14-row outbox quarantine.
