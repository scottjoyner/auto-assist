# Trace Live Watch Prospectus — 2026-10-09

**Status:** preregistered before implementation. Research-only, read-only.

## Question

Can a small operator-side watcher make trace-harvest failures easier to detect without
reintroducing the unbounded payload reads that PR #177 is designed to eliminate?

The watcher will consume only the gated metadata endpoint:

`GET /api/traces/{correlation_id}/timeline`

It will never call the legacy full-detail endpoint and will never call
`payload-preview`.

## Hypotheses

- **H1:** repeated polling can emit each newly observed event ID once while preserving
  chronological display order.
- **H2:** if more than one page of events arrives between polls, bounded keyset
  catch-up can recover the burst until a previously seen event is encountered.
- **H3:** if the watcher exhausts its configured page budget before reaching known
  history, it can report `gap_possible=true` instead of silently claiming complete
  observation.
- **H4:** a stalled trace can be identified from metadata timestamps without reading
  payload content.

## Safety and coexistence boundary

- no Neo4j writes;
- no trace event creation;
- no service restart or feature-flag mutation;
- no provider/model/fleet execution;
- no payload preview;
- no fallback to `GET /api/traces/{correlation_id}`;
- this prototype performs no credential handling; authenticated production ingress integration is a separate gate;
- tests use invented in-memory pages only.

The bounded timeline feature remains disabled by default. This tool does not authorize
turning it on in production.

## Proposed output

JSON Lines records with two schemas:

- `trace-live-event-v1`: metadata allowlist copied from the bounded timeline page;
- `trace-live-health-v1`: poll count, pages read, new event count, latest timestamp,
  lag, staleness and `gap_possible`.

No output field may contain `payload_json` or `payload_preview`.

## Reliability behavior

On startup the default is **tail semantics**: read one newest page, remember its event
IDs, emit no historical events, then watch for changes. `--emit-existing` is an
explicit opt-in for printing that first bounded page.
On later polls, the watcher follows `next_cursor` only until it encounters a
previously seen event. It stops earlier whenever possible. A configurable
`--max-pages-per-poll` is a hard read budget.

If the budget is exhausted while `has_more=true` and no known event was reached,
the health record must say `gap_possible=true`. It must not infer full custody or
retention.

## Acceptance

Synthetic tests must prove:

1. bootstrap does not dump unbounded history;
2. one new event is emitted once;
3. a burst larger than one page is recovered in order;
4. page-budget exhaustion produces a gap warning;
5. duplicate event IDs are not re-emitted;
6. payload-shaped fields from a malformed page are removed by the client allowlist;
7. state persistence, when requested, contains identifiers/timestamps only.

## Non-claims

Passing these tests does not prove production trace completeness, physical producer
identity, NAS custody, source authenticity, lossless harvesting, or authenticated
production usability. It creates a bounded live observation core and a better
diagnostic signal for the next harvester reliability slice.