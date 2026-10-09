# Prospectus — bounded trace detail and opt-in payload contract

**Date:** October 9, 2026 EDT. **Branch:** `feature/assistx-trace-detail-paging-20261009`, stacked on draft timeline PR #175. **Purpose:** resolve the outstanding unbounded server detail read documented in auto-assist issue #176 without widening graph or fleet authority.

## Known, non-blind baseline
Existing `GET /api/traces/{correlation_id}` calls `get_trace()` and returns *all* linked events plus their `payload_json`. PR #175 shows only the latest 80 locally in the browser, but this is a DOM-only bound; the network and graph reads are still unlimited.

## Falsifiable predictions
1. A **new** read-only endpoint, disabled by default, can return at most 80 (configurable 1–100) metadata-only events per request by fixed descending `(ts_ms,event_id)` keyset, with one extra row to indicate `has_more`. It must not project `payload_json`, `RETURN t`, or nested private fields. Pagination cursor must be versioned, bounded, strictly decoded, parameterized, and not treated as trusted authorization.
2. Equal timestamps and unique event IDs can be traversed without gaps or duplicates for a **stable synthetic snapshot** using strictly earlier `(ts_ms,event_id)` ordering. Concurrent appends/backfills remain outside snapshot guarantees; no total or retention completeness can be inferred from a page.
3. A **separate explicit POST** for a particular event ID can return a truncated payload preview only after authenticated operator request; event ID is in the request body, not a URL path. Response should flag preview truncation, not claim full custody.
4. Both endpoints require existing FastAPI authentication **plus an opt-in rollout switch defaulting to disabled**. Disabled/unauthenticated/invalid input must open no graph session. Until in-flight admission and trusted peer proxy gates #148/#149 are accepted, this switch must stay off.
5. Python fake-graph contract tests cover >1,000 records, identical timestamps, pagination edges, malformed cursor, tampered parameters, 401/422/503/404, event ownership, redacted-preview opt-in, and bounded Neo4j query timeout. No model, NAS, live graph or real traces.

## Limits and acceptance
This slice is source-owned server contract/test code **only**, not a claim that existing frontend stopped using the unbounded legacy endpoint. The legacy route will remain unchanged on this draft branch pending a coordinated client migration; do not enable new routes or deploy yet. No production services, source trace archives, notification routing, dispatch, provider quotas or Neo4j writes. Full historical retention issue #117 remains independent.
