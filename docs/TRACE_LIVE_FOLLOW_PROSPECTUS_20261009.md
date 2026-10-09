# Trace Live Follow Prospectus — 2026-10-09

**Status:** preregistered before implementation. Draft / research-only / feature stack OFF.

## Objective

Add an explicit operator-controlled live-follow mode to the authenticated trace
workbench already migrated to bounded metadata paging in PR #179 and integrated with
the green-CI source tree in PR #203.

This is a diagnostic surface for observing whether newly indexed trace metadata keeps
arriving. It is not a custody or producer-attestation mechanism.

## Predictions

1. Live follow remains **off by default** and causes zero polling requests until the
   operator explicitly enables it for a selected trace.
2. A normal poll performs one bounded metadata GET and emits no payload request.
3. A burst larger than one 80-event page can be caught up by following at most four
   keyset pages until an already-loaded event ID is reached.
4. If the bounded poll cannot reconnect to already-loaded history, the UI reports
   **possible gap** rather than claiming complete observation.
5. Authentication loss clears selected metadata using the existing workbench behavior;
   other paging failures pause live follow instead of silently retrying forever.
6. Payload preview remains human-disclosure-only and is never triggered by live follow.

## Read budget

Per selected trace:

- interval: 2 seconds while live follow is enabled;
- page size: 80 metadata events;
- maximum pages per poll: 4;
- maximum retained metadata events: 800.

No polling of the global index is required for live follow.

## UI semantics

The selected trace header will expose a `Follow live` / `Pause live` control and
metadata-only health text:

- newest indexed event age;
- last live poll time;
- newly observed event count;
- number of metadata pages read;
- possible-gap state.

“Newest indexed event age” is not source-to-harvester latency. The underlying source
snapshot remains mutable and historical retention remains unproven.

## Safety boundary

No Neo4j writes, no trace event creation, no payload polling, no legacy full-detail
fallback, no service restart, no feature-flag mutation, no model/provider/fleet
execution, and no production enablement.

The implementation must reuse the workbench's existing same-origin authenticated
request path. It must not introduce a new credential or secret convention.

## Acceptance

Synthetic Node and Chromium tests should demonstrate opt-in polling, bounded catch-up,
gap signaling, disable/selection cancellation, zero passive payload reads, mobile
layout, keyboard focus, and no legacy full-detail requests.

The slice remains blocked from production by the inherited physical query
concurrency/cancellation and trusted-ingress gates.