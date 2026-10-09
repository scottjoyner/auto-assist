# AssistX trace workbench: bounded client migration (2026-10-09)

## Scope and lineage
This draft stacks on #177, which stacks on progressive timeline #175. Neither child authorizes production activation. This client consumes the #177 disabled-by-default API contract and **never calls** legacy `GET /api/traces/{correlation_id}` for investigation details.

## Client decisions
1. Trace selection issues `GET /api/traces/{id}/timeline?limit=80`. It renders only the returned metadata (event ID/type, timestamp, source, task/dispatch/route/assignment IDs) and rejects schema inconsistencies, duplicate records, unexpected extra properties, disordered pages, and repeated cursors.
2. The explicit **Load up to 80 earlier events** control follows the opaque cursor. No background prefetch and no all-history totals. The frontend keeps at most 800 metadata records in a sliding window; refreshing the selection begins again at the newest page.
3. Context chips and event-type search operate **only over the loaded metadata window**. Up to 30 distinct values per field are displayed; surplus values are labeled as truncated. No graph-wide context completeness is implied.
4. Opening an individual event disclosure issues a separate `POST /api/traces/{id}/payload-preview` with `{event_id}`. This operator-triggered read returns a maximum of **4,096 characters** (not a 4,096-byte bound or PII redaction). Collapsing the disclosure clears the text; late responses from a prior selection or closed disclosure are ignored.
5. Auth failure clears the selected view; 503 from a disabled endpoint and invalid-page responses fail closed, **without** legacy detail fallback. A preview-scope 403 is displayed without discarding previously authorized metadata. Existing index-level outcome filters, permalink handling, task/registry evidence inspector, keyboard focus behavior, and identity-nonattestation language remain.
6. New **deny-only authentication mitigation**: both experimental timeline and preview routes additionally demand independently validated operator Basic credentials. The injected identity (which may originate in `TRUSTED_AUTH_HEADER`) is never enough by itself. The preview route additionally requires the authenticated Basic username in `ASSISTX_TRACE_PREVIEW_BASIC_USERS` (comma-delimited, default EMPTY/deny). If validation hook is missing, broken or denies, the handler refuses before Neo4j access with 503/403 and no-store error headers. This does **not** repair legacy paths or prove proxy stripping; do not set production flags until #148/#149 and operator approval are satisfied.

## Validation
- Reused the existing 25 Node VM regression definitions after routing their synthetic legacy fixtures through an in-test metadata-only paging adapter.
- Added four synthetic checks: no passive payload / no legacy GET; default-disabled 503 fail-closed; invalid schema / extraneous payload rejection; equal-timestamp 1001-event page navigation.
- **x1-370 native Node.js 22.23.3: 30/30 passed** on an isolated PR checkout. This supersedes the earlier V8 adaptation.
- **x1-370 Python 3.12.3: 135/135 passed** covering detail pages, context, outcome, task evidence and attestation (one unrelated Starlette deprecation warning).
- **x1-370 real headless Chromium/Playwright synthetic fixture: 4/4 passed**: 375, 768 and 1440px event paging, equal timestamps, outcome/context/type filtering, deep links, deliberate preview POST, clearing disclosure, and keyboard focus. Mobile axe WCAG 2.1 A/AA scan found zero automated violations. No live credentials or production endpoint used.
- Browser and Python tests were run after adapting the original full-detail synthetic fixture to bounded metadata pages; a local ephemeral Playwright package installation was not committed. These are not authenticated operator/device tests, manual screen-reader certification, or physical admission evidence.
- No deployment, Neo4j graph access, NAS write, fleet execution, router mutation, or production flag activation was performed.

## October 9 authentication and CI reconciliation

- **Risk identified:** the app's legacy `_auth_user_from_credentials` prefers `TRUSTED_AUTH_HEADER` to Basic credentials. A header-only accepted identity is not proof the reverse proxy scrubbed a client-controlled header. Only the NEW paged timeline/preview routes have the interim independent Basic credential gate; old trace GET, the legacy full-detail route and other app paths remain outside this mitigation.
- **Read-only x1-370 listener inventory:** the AssistX container's published host port is loopback-only `127.0.0.1:8000`. Tailscale Serve on tailnet-only port 8443 routes selected `/api/v1/...` requests and `/health` to 8000, while its root route proxies another local service on 8081. This **does not** demonstrate all forwarded headers are stripped, whether 8081 forwards trace paths, or the identity source of alternate local clients. No credentials or traffic payloads inspected.
- The CI `unit` job for PR #179 and parent #177 reports the identical inherited summary **77 failed / 800 passed / 59 deselected**, spanning 15 non-trace test modules; both also fail recovery-canary. These failures remain release blockers rather than being waived by the targeted passing tests. Avoid hiding the suite behind a narrowed CI filter.
- Latest native local checks (isolated x1-370 worktree): **135 Python**, **30 Node**, and **4 synthetic Chromium/axe** checks passing. No authenticated production browser, physical multiworker graph concurrency, remote query termination, Redis failover, or role policy attestation.

## Required gates before release
- Confirm exact-head GitHub CI and rerun the local suites if implementation code changes; complete a manual screen-reader/keyboard review. Native Node, focused Python and synthetic Chromium suites are now passing locally.
- Browser-authenticate and verify request-level network traces: one metadata page at selection, exactly one additional page per explicit click, **zero** legacy full-detail calls, zero payload requests prior to deliberate disclosure, and a single preview request per intentional expansion.
- Negative cases: malformed/oversized cursors, 401/403, 422, 429, 503, service restart, rapid selection switches, closing a disclosure before the response, duplicate-timestamp cursor continuation, request cancellation/time budget and memory cap.
- Close #148 physical query concurrency/cancellation admission and #149 trusted peer/proxy/auth boundaries. Decide role-scoped payload-preview privacy permissions and whether the legacy endpoint should be retired/gated for all callers (the frontend does not close backend exposure on its own).
- Obtain human operator approval before any enablement of `ASSISTX_TRACE_DETAIL_PAGING_ENABLED=1`; keep #176 and release #123 open until these are evidenced. No full-source→spool→NAS custody guarantee and no verified physical executor identity.

## Research limits
Keyset pages are not a signed or immutable snapshot. Events inserted while paging can shift what an operator sees. No historical completeness is proven; timestamps and source labels are unverified producer data. The preview query limits returned characters, not necessarily physical bytes read inside Neo4j or query execution cost. The legacy backend detail route remains exposed to authenticated callers until separately addressed.
