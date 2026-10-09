# Observations — progressive, type-scoped trace timeline (October 9, 2026)

**Prior hypothesis:** `TRACE_TIMELINE_DENSITY_PROSPECTUS_20261009.md` was recorded before source edits. **Base:** reviewed trace/evidence stack [auto-assist PR #140](https://github.com/scottjoyner/auto-assist/pull/140). **Status:** proposed frontend-only review slice, no production deployment.

## Implemented
- Render the **latest 80** event summaries from a loaded trace, in chronological order. Progressively show up to **80 earlier** events per button press. The timeline reports both **rendered** and **matching** event counts, and the existing header still reports matching vs total *loaded* events.
- Add an explicitly local **event-type** search; it is a case-insensitive substring match against top-level `event_type` only. It never reads `payload_json` or other event fields to search, never performs backend requests, and does not claim full archival search.
- Type search and existing provenance-limited context chip filtering intersect; changing traces clears both type search and the render batch, while the existing read-only on-demand registry evidence inspector stays intact. Every event payload remains a collapsed `details` disclosure until opened and is cleared on close.
- Show visible empty states and count labels for no matches. On rerender, keyboard focus returns to the type input after typing/clearing and to the show-earlier control (or type input) after batch expansion.
- Preserve all current authentication, on-demand evidence, no-authority, no-verified-node claims, index global filters and historical retention caveats. No new API endpoints, payload indexing, NAS work or graph queries.

## Predicted versus observed
| Preregistered gate | Observation |
| --- | --- |
| 1000-event trace initial DOM | **80 timeline cards**, latest records, not all 1000 |
| Progressive preceding batch | **160 cards** after first "earlier" action; no new GET |
| Search event-type only | Case-insensitive, no raw payload text in HTML; no new GET |
| Combine recorded task context + type | Intersection of scoped filters and accurate loaded-count labels |
| Reset and keyboard focus | Type search resets with selected trace; input remains focused after text edit |
| Mobile/tablet/desktop | **375px, 768px, 1440px real Chromium** synthetic fixtures passed with no horizontal overflow |
| Automated accessibility | Axe WCAG 2.1 A/AA mobile fixture reported **zero violations**, not a manual screen-reader certification |
| Prior regression suite | **25 Node tests passed** (22 previous + 3 new) |
| Existing backend context/evidence/attestation/global outcome tests | **102 Python tests passed** with one unrelated TestClient deprecation warning |

**Synthetic real-browser suite: 4 passed**, of which three cover viewport/density/type search and one axe scan. Browser requests were fully intercepted to an invented origin; no live AssistX service, protected trace data or credentials were fetched.

## Critical gaps

1. **Only DOM rendering is bounded.** The backend `get_trace()` still reads all events in a group, and returns `payload_json` in the JSON response before any disclosure. Network bandwidth, graph scan cost, response memory, real event count, private payload transport and end-to-end retention remain unbounded/unproven. This is a next **server-side budget + authenticated pagination** decision, not solved by the frontend.
2. Type filter does **not** support arbitrary text search or task/node metadata search. The UI explicitly labels it a local event-type-only filter.
3. Existing source labels remain unverified producer labels and registry IDs remain unverified correspondence, not physical node/agent attestation. Do not generate links to execution controls.
4. Real authenticated browser and screen-reader testing against the deployed app remain open; all current data is synthetic and the PR is unmerged/draft.
5. Evidence/custody claims remain under separate full-history and source attestations; local trace timeline counts must not be interpreted as proof of all preserved tool calls.

## Reproduction
On x1-370 in `/home/scott/git/wt-assistx-trace-timeline-density-20261009`:

```bash
node --check static/js/traces.js
node --test tests/test_trace_investigation_ui.cjs
PYTHONPATH=src python3 -m pytest -q \
 tests/test_trace_outcome_filter.py tests/test_trace_context.py \
 tests/test_trace_task_evidence.py tests/test_trace_attestation.py
NODE_PATH=/home/scott/git/OmniRoute/node_modules \
 node --test tests/test_trace_timeline_browser.cjs
```

The Chromium tests use an already-installed Chromium executable from the local Playwright cache; no download or production deployment is required.

## Next acceptance
- Design source-owned paginated read-only event APIs and authenticated rate/size limits without losing audit integrity or confusing a window with full history. Implement synthetic max-event/fault cases first.
- Review in a deployed authenticated browser under a trusted session at 375px/tablet/desktop, including keyboard navigation, screen readers and event-field disclosure privacy.
- Reconcile stacked drafts before merge; no deployment, no real trace payload model ingestion, no NAS/archive or route-execution modifications.
