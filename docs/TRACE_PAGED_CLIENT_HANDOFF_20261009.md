# AssistX trace workbench: bounded client migration (2026-10-09)

## Scope and lineage
This draft stacks on #177, which stacks on progressive timeline #175. Neither child authorizes production activation. This client consumes the #177 disabled-by-default API contract and **never calls** legacy `GET /api/traces/{correlation_id}` for investigation details.

## Client decisions
1. Trace selection issues `GET /api/traces/{id}/timeline?limit=80`. It renders only the returned metadata (event ID/type, timestamp, source, task/dispatch/route/assignment IDs) and rejects schema inconsistencies, duplicate records, unexpected extra properties, disordered pages, and repeated cursors.
2. The explicit **Load up to 80 earlier events** control follows the opaque cursor. No background prefetch and no all-history totals. The frontend keeps at most 800 metadata records in a sliding window; refreshing the selection begins again at the newest page.
3. Context chips and event-type search operate **only over the loaded metadata window**. Up to 30 distinct values per field are displayed; surplus values are labeled as truncated. No graph-wide context completeness is implied.
4. Opening an individual event disclosure issues a separate `POST /api/traces/{id}/payload-preview` with `{event_id}`. This operator-triggered read returns a maximum of **4,096 characters** (not a 4,096-byte bound or PII redaction). Collapsing the disclosure clears the text; late responses from a prior selection or closed disclosure are ignored.
5. Auth failure clears the selected view; 503 from a disabled endpoint and invalid-page responses fail closed, **without** legacy detail fallback. Existing index-level outcome filters, permalink handling, task/registry evidence inspector, keyboard focus behavior, and identity-nonattestation language remain.

## Validation
- Reused the existing 25 Node VM regression definitions after routing their synthetic legacy fixtures through an in-test metadata-only paging adapter.
- Added four synthetic checks: no passive payload / no legacy GET; default-disabled 503 fail-closed; invalid schema / extraneous payload rejection; equal-timestamp 1001-event page navigation.
- **29/29 passed in an isolated V8-based adaptation of the Node VM harness on the final bounded-window client revision**, and JavaScript syntax compilation passed. This is NOT an authentic `node --test` execution, native Chromium test, or physical authenticated browser acceptance.
- The backend's previously reported 131 Python and 25 Node tests belong to **#177**, not to this child's validation.
- No deployment, Neo4j graph access, NAS write, fleet execution, router mutation, or production flag activation was performed.

## Required gates before release
- Run native `node --test tests/test_trace_investigation_ui.cjs` and Python regressions on the *final commit*. Run the existing 375/768/1440px Playwright suite; adapt its synthetic fixture to paged transport if necessary and inspect manual keyboard/focus/screen-reader behavior.
- Browser-authenticate and verify request-level network traces: one metadata page at selection, exactly one additional page per explicit click, **zero** legacy full-detail calls, zero payload requests prior to deliberate disclosure, and a single preview request per intentional expansion.
- Negative cases: malformed/oversized cursors, 401/403, 422, 429, 503, service restart, rapid selection switches, closing a disclosure before the response, duplicate-timestamp cursor continuation, request cancellation/time budget and memory cap.
- Close #148 physical query concurrency/cancellation admission and #149 trusted peer/proxy/auth boundaries. Decide role-scoped payload-preview privacy permissions and whether the legacy endpoint should be retired/gated for all callers (the frontend does not close backend exposure on its own).
- Obtain human operator approval before any enablement of `ASSISTX_TRACE_DETAIL_PAGING_ENABLED=1`; keep #176 and release #123 open until these are evidenced. No full-source→spool→NAS custody guarantee and no verified physical executor identity.

## Research limits
Keyset pages are not a signed or immutable snapshot. Events inserted while paging can shift what an operator sees. No historical completeness is proven; timestamps and source labels are unverified producer data. The preview query limits returned characters, not necessarily physical bytes read inside Neo4j or query execution cost. The legacy backend detail route remains exposed to authenticated callers until separately addressed.
