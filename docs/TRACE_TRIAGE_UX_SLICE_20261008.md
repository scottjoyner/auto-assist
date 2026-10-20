# AssistX Trace History — bounded investigation UX slice
**2026-10-08 EDT. Implementation branch:** `feature/assistx-trace-investigation-20261008`, worktree isolated from the dirty provider-burn checkout.

## Current verified deficiencies
- The existing `/traces` list presents status badges for the **current page** adjacent to an index-wide count with no label distinguishing scopes.
- Trace rows are mouse-only `div` elements, previous/next controls have arrow-only labels, and no deep-link or keyboard navigation exists.
- Event payloads are eagerly rendered as JSON, although trace payloads can include operational context. The proposed slice makes payload expansion deliberate and local to the selected event.
- Search is for correlation IDs only, with no honest filter explanation or stateful error/retry/refresh behavior.
- Older responses can overwrite more recent selections or searches; no fetch race fencing.
- View uses generic paragraphs and two cards without strong small-screen operator hierarchy or focus states.
- Live `/provider-usage` and `/api/fleet/provider-burn` returned **404** on October 8, although the staged frontend links to them. Avoid dead links until that separate route is deployed.

## Predicted acceptance
1. True operator layout: prominent trace navigation, labeled filters and pagination, clear selection summary and independently visible detail pane, responsive down to 375px.
2. Keyboard-accessible list buttons, proper focus states, status/error live regions, loading/empty/retry states.
3. A trace permalink `?trace=<correlation_id>` opens its detail even when not among the current 50 loaded list entries, without changing server auth or exposing event payload in URLs. Copy only the ID on click.
4. Current-page failure counts and local outcome filter state must be explicitly labeled. **No claim** of global outcome aggregates or full historical event retention.
5. Hide event payloads behind explicit per-event disclosures; HTML escaping and no automatic logging/copying. Warn that route traces are an **index**, not the complete nontrimming audit ledger.
6. Defend against stale asynchronous list/detail responses; manual refresh without server writes. Do not add routing, admission or execution buttons.
7. Mocked browser tests must cover deep linking, keyboard selection, search races, label semantics, payload privacy, mobile usability and auth failure.

## Constraints
- Reuse existing authenticated `/api/traces` and `/api/traces/{correlation_id}` read-only GET endpoints. Existing unauthenticated requests receive HTTP 401 (observed). Do not change auth, Neo4j data, fleet services, provider telemetry schemas, NAS, or running deployments.
- Preserve unrelated local dirty files in the original burn-dashboard worktree.
- Branch/review first. Live UI release requires independent operator deploy approval and rollback plan.

## Observed implementation and acceptance (October 8, 2026)
- Replaced the trace list/timeline HTML and JS with a responsive two-pane operator workbench; added new scoped stylesheet. No API handlers changed.
- Seven dependency-free Node VM contract tests passed: accessible structure, global ID matches vs current-page failure counts, direct-link selection outside the loaded index page, delayed event-field disclosure, local-only outcome filter, explicit 401 recovery and list/detail stale-response fencing.
- The current live service returns HTTP 401 unauthenticated on `/traces`, `/api/traces`, `/fleet-dashboard` and `/control-room`; no auth bypass was attempted. The staged `/provider-usage` and `/api/fleet/provider-burn` paths returned **404**, so the new UI marks the usage navigation as staged rather than linking to a dead route.
- Two independent existing provider-burn Python test modules passed 10 tests on the burn-feature checkout. Dashboard integration test collection failed in that checkout due to a pre-existing missing local `langgraph` dependency; no conclusion about app startup or production deployment was drawn.
- There was no Playwright/Chromium visual browser run on x1-370 due to unavailable module and restrictive snap Chromium. The VM contract tests check behavior and static responsive CSS, not pixel appearance or real keyboard/browser interoperability. These remain release checks.
- This is a review-only frontend slice. No service restart, template mount edit, Neo4j mutation, provider admission, trace retention change, or NAS write occurred.

## Follow-on gaps explicitly not closed
- Global failed-trace querying across all 85k groups requires a separately budgeted, tested server-side filter; local outcome selection is truthful and page-scoped only.
- AssistX history is an investigative router-event index; full historical tool-call arguments/outputs, durable retention and custody remain a separate acceptance gate.
- Promote provider usage navigation only after the authenticated route is deployed, checked and owned; it is currently staged.
- Verify visual behavior at 375px, tablet and desktop in a real browser and authenticated trace page before any production release.
