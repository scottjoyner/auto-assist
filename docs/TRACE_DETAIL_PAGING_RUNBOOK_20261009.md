# Runbook — trace event page and opt-in payload prototype
**Date:** October 9, 2026 EDT. **Scope:** draft backend-only acceptance under [issue #176](https://github.com/scottjoyner/auto-assist/issues/176); default-disabled, not a release.

## Reproduce synthetic checks without touching fleet data

```bash
cd /home/scott/git/wt-assistx-trace-detail-paging-20261009
PYTHONPATH=src python3 -m pytest -q \
 tests/test_trace_detail_pages.py tests/test_trace_outcome_filter.py \
 tests/test_trace_context.py tests/test_trace_task_evidence.py \
 tests/test_trace_attestation.py
node --test tests/test_trace_investigation_ui.cjs
```
**Oct 9 observed:** 131 Python tests and 25 Node VM tests passed. No production DB connection was used by tests. The two new endpoints are disabled by default.

## Proposed read API (NOT enabled)

`GET /api/traces/{correlation_id}/timeline?limit=80&cursor=<opaque>` returns `schema:trace-event-page-v1`, at most 100 events per call, `has_more` and `next_cursor`. Each event contains only event ID, timestamp, bounded type/source label and bounded scalar context IDs; no `payload_json`. For user-facing counts, `total_indexed_events:null` and `source_snapshot_immutable:false`; do not imply that a page or the UI index is complete audited history. Cursor is a versioned, length-restricted encoding of timestamp+event ID; it is a navigation token, **not signed authentication**.

`POST /api/traces/{correlation_id}/payload-preview` with JSON body `{"event_id":"..."}` returns at most 4,096 characters of the single owned event's payload, with truncation status, only after an explicit operator action. **This is not a redaction/PII entitlement check.** Both endpoints add `Cache-Control: no-store, private` and use 4-second read-only Neo4j transaction timeouts.

Existing `GET /api/traces/{correlation_id}` is unmodified and still potentially returns unlimited events and their sensitive payloads. **Do not enable the new flag or deploy until the client is migrated and the old unbounded route is addressed in a reviewed, backwards-compatible manner.**

## Safety controls and acceptance
- Only enable in an authorized staging environment after `api.py` injects its production auth dependency; if missing, route denies with HTTP 503. No separate blanket authorization to expose previews is established.
- Do not treat `ASSISTX_TRACE_DETAIL_PAGING_ENABLED=1` as production approval. Physical Neo4j query-budget fence, cancellation/expiry proof and trusted ingress/peer fairness remain open in [#148](https://github.com/scottjoyner/auto-assist/issues/148) and [#149](https://github.com/scottjoyner/auto-assist/issues/149).
- Browser must explicitly fetch preview **only after** opening one event disclosure, never cache or print payload, and clear it on collapse. No automatic fallback to old unbounded route.
- Use a separate disconnected synthetic database to validate actual Cypher. Initial Neo4j **5.26.26** isolated smoke with 1,001 invented events compiled/executed both read queries; shell-inclusive latency was not a driver/SLA measurement. The disposable container was removed.
- Maintain source-generation and snapshot uncertainty. Concurrent writes/backfills can move pages; distinct stable event IDs and timestamps are needed for exact per-snapshot navigation.
- Keep full historical nontrimming custody requirement [#117](https://github.com/scottjoyner/auto-assist/issues/117) independently blocked.
