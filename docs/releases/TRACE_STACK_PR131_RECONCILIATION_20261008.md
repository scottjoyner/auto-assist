# Trace investigation stack conflict reconciliation — October 8, 2026

**Base:** auto-assist draft #129 d76072e4333e21857074b57f46b8592018fa8ed7,
which preserves #121 -> #122 -> #124 -> #128 -> #129. This is a separate
read-only integration preview; no production deployment or merge is approved.

**Pre-test prediction for the stacked replay:** The small #131 fix can be
replayed after #129 without reverting server-side global outcome predicates,
changing attestation, auto-routing, task claims or trace authority. The
stacked UI will preserve focus across selection, retry failed detail on
Refresh, clear sensitive evidence on failed index authentication, and avoid
stale response resurrection. Its Node UI tests and locally simulated
Chromium checks should pass, while API tests remain read-only and
self-contained (no Neo4j writes or production credentials).

**Observed merge conflicts:** The #131 patch touched both traces.js and the
combined UI test file, which are also modified by #122, #124 and #128. A
straight git apply --3way produced conflicts in outcome state initialization,
paintList and detail selection, plus the test suffix. We intentionally
preserved #129 global server-side outcome filtering (not the old #121
client-side page filter), on-demand registry inspection, provenance-limited
context navigation, and currentTrace invalidation. We imported the focus
transfer and detailLoaded retry semantics from #131. The #129 test suite
remains intact; two #131 failure-recovery tests are appended.

**Remaining gates:** This preview does not establish physical executor
identity, authoritative source MAC attestation, actual Neo4j performance,
production authenticated browser login, multi-host trace lease authority,
provider quota, remote receipt custody or authorization to activate agents.
The temporary browser harness intercepts GETs in-process; it is not a live
AssistX API integration. Do not silently merge this branch into a live
release without reviewing the complete test evidence and upstream stack.

**Verification commands (explicitly no hosted provider calls):**
- node --check static/js/traces.js
- node --test tests/test_trace_investigation_ui.cjs
- PLAYWRIGHT_CHROMIUM_EXECUTABLE=/path/to/cached/chromium-headless-shell \
  /path/to/isolated/playwright-python tests/test_trace_browser_synthetic.py
- PYTHONPATH=src python -m pytest --noconftest -q
  tests/test_trace_outcome_filter.py tests/test_trace_context.py
  tests/test_trace_task_evidence.py tests/test_trace_attestation.py

No background services or credentials may be started to make a test pass.

## Recorded local observations after preregistration (2026-10-08)

- 22/22 native Node trace workbench tests passed after resolving four JS
  and one test-suffix three-way merge conflicts. The original #129 suite was
  retained, and both #131 error-recovery tests were appended.
- 21/21 loopback-intercepted synthetic Chromium checks passed, covering
  375/768/1440-pixel layout, keyboard focus, hidden payload disclosure,
  deep links, 401 auth-loss clearing, 429/503 retry, detail retry and GET-only
  requests. This used a disposable Python virtual environment with
  Playwright 1.58.0 and an already cached Chromium headless shell.
- 102/102 focused Python tests passed against the checked-out stacked source
  using PYTHONPATH=src, --noconftest and no database daemon. Tests covered
  global trace outcomes, provenance-limited context, registry task evidence
  and strict research attestation claims.
- Syntax checks passed. No provider, SSH, NAS or graph mutation in any test.
- A first backend test command mistakenly selected a Python environment
  without pytest; the correctly isolated system Python run then passed.
  That failed launch was a test-environment prerequisite, not a failed
  source assertion.
- All testing remains **fixture or isolated API test only**; no live operator
  authentication or production graph query load was observed.

## Disposition
Research integration preview may be reviewed as a stacked draft against #129;
do not merge or deploy until branch-base/source closure, live auth browser and
backend performance acceptance are independently verified. The execution
and provider admission gates are unchanged.
