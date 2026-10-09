# Trace Live Follow Observations — 2026-10-09

**Result:** source/UI acceptance PASS. Production activation remains NO-GO.

## Implementation

The trace workbench now has an explicit `Follow live` control for the selected trace.
It reuses the existing same-origin authenticated request path from the bounded paged
client and performs metadata-only polling.

Behavior:

- off by default;
- one newest metadata page per normal 2-second poll;
- up to four 80-event pages only when catching up a burst;
- stops paging as soon as an already-loaded event ID is reached;
- reports **possible observation gap** if bounded catch-up cannot reconnect to known
  history;
- merges newly observed metadata into the selected timeline with duplicate suppression;
- retains at most 800 event metadata records client-side;
- labels the newest indexed event age without calling it harvester latency;
- pauses on paging errors instead of retrying indefinitely;
- authentication loss uses the existing metadata-clearing behavior;
- payload previews remain explicit disclosure-only POSTs.

No global index polling was added to live mode.

## Evidence

Native Node VM workbench contracts:

`node --test tests/test_trace_investigation_ui.cjs`

**32/32 passed**, including new opt-in/pause and four-page possible-gap cases.

Trace backend/security regressions:

`python3 -m pytest -q tests/test_trace_detail_pages.py tests/test_trace_outcome_filter.py tests/test_trace_context.py tests/test_trace_task_evidence.py tests/test_trace_attestation.py`

**135/135 passed** with one existing Starlette TestClient deprecation warning.

Synthetic Chromium, using the same pinned browser family already used by prior trace
acceptance:

`ASSISTX_TRACE_TEST_CHROMIUM=/home/scott/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome node --test tests/test_trace_timeline_browser.cjs`

**5/5 passed**:

- 375px;
- 768px;
- 1440px;
- axe WCAG 2.1 A/AA mobile audit: zero automated violations;
- authenticated same-origin live-follow opt-in/pause contract.

The first browser attempt in the new worktree could not resolve a local Playwright
module/browser cache. No dependency was installed or downloaded. The test was rerun
using the already-existing #179 acceptance Node modules and pinned Chromium binary.

## Negative / failure-mode evidence

The synthetic burst test injects 321 unseen events ahead of the known anchor. Live
follow issues exactly four bounded timeline reads, fails to reconnect to known history,
and surfaces `possible observation gap`.

The normal live test proves:

- no timeline poll before explicit operator opt-in;
- no payload-preview request during live polling;
- no legacy full-detail GET;
- a new metadata event is rendered;
- pausing cancels the background poll timer.

## Boundaries

This does **not** prove:

- source-to-harvester latency;
- lossless source-to-index transport;
- immutable source history;
- physical producer/node identity;
- NAS/full-fidelity custody;
- production ingress authenticity;
- safe physical Neo4j read concurrency/cancellation.

No production trace reads, graph writes, service restarts, feature-flag changes,
provider/model calls, NAS changes, or fleet execution were performed.

## Next gate

Keep the feature stack OFF. The next acceptance should be an authenticated physical
browser/network witness showing the bounded request pattern and zero passive payload
reads, after the inherited query-admission/cancellation and trusted-ingress gates are
satisfied.