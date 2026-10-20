# Trace workbench: synthetic Chromium acceptance evidence
Date: 2026-10-08
Scope: *in-process synthetic fixtures*, not real authenticated staging or production.

## Pre-run predictions
Source: PR #131's preceding acceptance matrix (also recorded in the independent `docs/ASSISTX_OBSERVABILITY_NEXT_SLICE_20261008.md` prospectus branch).
- Three viewport widths should not overflow.
- Keyboard Enter should preserve visible focus when list DOM is re-rendered.
- Event payload is rendered only after a deliberate disclosure and cleared on collapse.
- Deep-linked correlation IDs absent from the current 50-row page should fetch directly.
- Index 401 must clear stale details, disable Copy ID and prevent residual exposure.
- Index 429/503 should preserve a retry affordance; failed selected detail should be refetched on Refresh.
- All requests must be GET; provider burn navigation must stay staged.

## Fixture and command
- Repository: `scottjoyner/auto-assist`
- Branch: `fix/assistx-trace-ui-recovery-20261008`
- Final tested SHA: `0e220480fc52cbca1375785a7925582e4f82246c`
- Device: x1-370; isolated checkout `/tmp/assistx-trace-acceptance-Ifg31FFd/repo`.
- Browser: cached Chromium headless shell (Playwright browser package chromium_headless_shell-1228).
- Python Playwright: installed only into isolated `/tmp/assistx-playwright-t0fJckEj/venv` (v1.58.0); no system install.
- All URLs, HTML, JS, CSS and API responses are served directly by Playwright's route fulfillment at a mocked 127.0.0.1 origin. **No live server, login, provider request, fleet dispatch or authenticated session accessed**.
- Node command: `node --test tests/test_trace_investigation_ui.cjs`.
- Browser command: `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/path/to/chrome-headless-shell /path/to/venv/bin/python tests/test_trace_browser_synthetic.py`.
- Browser setup requires Python Playwright and an installed Chromium executable; this dependency is not activated in the production service.

## Results
- JavaScript VM contract: **9 pass, 0 fail**.
- Chromium acceptance: **21 PASS checks, 0 fail**.
- Viewports: 375x812, 768x1024, 1440x900 all no horizontal overflow; provider Usage & burn remains staged.
- Keyboard Enter selects the second row and restores focus with visible outline.
- Collapsed payload absent; opening renders only synthetic canary data; closing clears it; no additional fetch on disclosure.
- Direct link opens an out-of-first-page trace.
- Index 401 clears selected detail and disables copy.
- HTTP 429 and 503 index errors offer Retry; retry restores list.
- Detail 503 followed by Refresh succeeds.
- Every observed synthetic request was GET.

## Failure lineage and correction
- Initial browser attempt at commit `f8f0feb2c753e5ae9080f1ade0bce3aeee8052aa` failed keyboard-focus continuity after the six viewport/staged-navigation checks.
- Debug showed `document.activeElement` was `body` after Enter while the correct row was selected. Root cause: code captured `focusedCid` but never transferred focus after `innerHTML` replacement.
- Corrected at commit `5c79fdb7ee6d85215fe26290fb19e439b4af48a5`, passing the initial 15 browser checks.
- Additional focus outline, disclosure-no-fetch, HTTP 429/503 retry checks added. One intermediate synthetic test erroneously retried a detail without first inducing a 503; corrected test setup at `0e220480fc52cbca1375785a7925582e4f82246c`. That failure was in fixture sequencing, not evidence of an application regression.

## Strict remaining gates
- Independent authenticated staging run, existing index/detail auth and rate limit middleware, 401/403 and actual 429 behavior, screen reader and mobile touch/device accessibility, security review, source provenance, independent full-fidelity custody.
- Do not infer production endpoint behavior or quota accuracy from an in-process mock.
- PR #131 remains draft and stacked on #121; no merge/deploy/rebase is authorized by this evidence alone. Provider Usage & burn routes remain staged.
