# Prospectus — global trace outcome search (October 8, 2026)

**Purpose:** make the AssistX trace investigation workbench search the *entire* authenticated trace index by outcome (failed/completed/open) without pretending a 50-row page filter is global. **Branch:** `feature/assistx-trace-global-filter-20261008` stacked on draft UI PR #121.

## Predicted behavior before implementation

1. Existing `GET /api/traces` with no outcome remains backward compatible. An optional validated `outcome=failed|completed|open` adds an exact global filter; unknown outcome is rejected with HTTP 422 rather than interpreted as Cypher.
2. The filtered `total` is computed across the full set of trace groups with events, and the page data uses the **same inclusion predicate**, not only post-hoc filtering of 50 rows.
3. Failed wins over completed if a trace has both event types. Completed requires a completed/accepted event *and no failed event*. Open has neither. A group with no events does not enter filtered results.
4. All searches are read-only Neo4j queries with fixed predicates and bound `$search`, `$offset`, `$limit` parameters. No event payloads are queried for the index. The page size remains <=200, UI uses 50.
5. Trace page selector switches from local filtering to global server queries, resets page offset on selection, supports filtered direct links `?outcome=failed&trace=...`, and never changes trace authority. The current-page failure metric remains labeled as current page.
6. Tests assert query semantics, parameter binding, bounded pagination, blank/no-event cases, precedence, UI filter pagination, and stale-response safety. Browser visual and live 85k-group query performance remain separate release gates.

## Resource and authority boundaries

Neo4j can process many events across ~85k groups; adding a global outcome filter may be more expensive than simple correlation search. This draft will **not** execute unrestricted full-history queries against the live production graph or claim measured latency without a gated, budgeted authenticated acceptance. The filter is read-only but requires a production plan/profile, timeout and rate-limit review before release.

No source trace payloads, NAS, services, routing, provider admission, OpenTunnel, shell dispatch or production writes. Keep PR #121 and this new slice as drafts.

## Observations and measured test results (October 8, 2026)

**Implemented:** a fixed, allowlisted global `outcome=failed|completed|open` query parameter on the existing authenticated GET `/api/traces` route. Server-side outcome predicates are applied to **both** the global count and page query, before pagination, with exact failure-first precedence. The route caps `limit` at 200, rejects negative offsets/unknown outcomes/ID searches >128 characters (HTTP 422), and uses two read-only Neo4j statements with a **4-second transaction timeout each**. Queries project only trace group ID, timestamps, event type set, event count and derived outcome; no event payloads.

**Frontend change:** the existing outcome selector now requests global historical filtering and resets paging. URL `?outcome=failed&trace=...` preserves both the global filter and a selected trace across direct links. The UI rejects older backend responses that cannot attest to applying the filter, and shows metrics as **unknown**, not 0, on API errors. The failure metric remains **failures on the current page**, while the global total means matching trace groups across the entire indexed history.

**Tests observed:** 17 offline Python query/route contract tests passed (including a FastAPI TestClient simulation of injected production auth, result total-vs-limit, precedence, search binding, timeout, invalid inputs and no payload projection); 11 Node VM UI contract tests passed (including older-backend fail-closed behavior, global-filter deep link, page two, and stale response suppression). One unrelated Starlette/TestClient deprecation warning appeared. The previous 7 UI tests were expanded to 11; do **not** add 11 to 7 as separate suites.

**Important interpretation of auth testing:** the bare swarm router's `_default_auth` has a permissive fallback for isolated import. The production `api.py` injects its actual authentication dependency, and the live service returned HTTP 401 without credentials in the earlier checkpoint. The route integration test **explicitly injected** a rejecting auth dependency. A bare router fixture does not by itself validate production middleware. No access bypass or live trace content was attempted.

**Limitations / roll-forward gates:** actual Neo4j 85k-group query latency, timeout behavior, indexes, concurrent readers and plan cost were **not measured**, and no real browser visual/regression test was available in the x1-370 environment. The global failure filter can require scanning many event memberships. Before deployment, run a restricted, operator-approved `EXPLAIN`/`PROFILE` on synthetic/staging graph (never broad live PROFILE by default), stress index count/page with a large fixture, enforce API budgets/rate limits, and verify proper authorization in the deployed API. The current live `/traces` service was **not** updated. Full-fidelity 2025-08-08 tool-call retention is still separate from this router-event index.

**Decision:** bounded code and synthetic acceptance passed; keep this stacked PR draft until performance and authenticated browser verification. No writes to Neo4j, NAS, collectors, provider routing, fleet execution or production services.
