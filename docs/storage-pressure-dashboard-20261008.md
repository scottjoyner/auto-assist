# Fleet storage-pressure dashboard integration — 2026-10-08

**Purpose / decision:** expose already collected x1-370 SSD and NAS L5 admission
evidence to authenticated AssistX operators without allowing storage mutations.
The producer is a 15-minute systemd user timer, and the consumer is an
authenticated read-only router in the AssistX feature worktree.

## Architecture

Producer:
- Monitor: /home/scott/git/ssd4tb-nas-offload/fleet_storage_status.py
- Root/Beelink identity and L5 gates: the earlier verified offload scripts
- Sanitized artifact: /home/scott/git/auto-assist/artifacts/storage-pressure-status.json
- Raw status and Prometheus metrics remain under the x1 user state directory.

Consumer (this worktree):
- src/assistx/storage_pressure_routes.py, GET /api/fleet/storage-pressure
- Added via src/assistx/api_router.py behind existing auth dependency.
- Rendered in templates/fleet_dashboard.html with textContent only, no control buttons.
- Snapshot may be mounted in the existing AssistX /app/artifacts application volume.
- Consumer has no source/NAS/SQLite, SSH, provider, dispatch or cleanup access.

## Prediction before experiment

A whitelisted single-file projection can accurately show SSD pressure, NAS
capacity, current admission and the human approval barrier while refusing
unknown, stale or malformed observations. It must not leak source paths, tokens,
raw audit data, or transform provider-capacity GO into source-deletion approval.

## Evidence and acceptance

- Producer generated a real 557-byte artifact with mode 0644, a whitelisted
  schema, source_release_blocked=true, and automatic_cleanup_allowed=false.
- At the first accepted snapshot, SSD usage was ~95.3% and NAS L5 was HOLD.
- Consumer returned state=fresh when independently pointed at the actual artifact.
- Five focused storage API and UI tests passed plus four existing provider-burn
  tests, including auth 401, private/no-store, HTML text-only render hook,
  stale NO admission, source-release BLOCKED even on GO, oversized and
  malformed fail-closed, and no raw filename or secret field exposure.
- Inline JS passed node --check. The offload/projector suite passed 20 tests.

## Production gate is not yet crossed

GET http://127.0.0.1:8000/api/fleet/storage-pressure currently returned 404
before deployment. Existing /fleet-dashboard requires auth (401 without it),
so full end-to-end user-visible UI acceptance requires a reviewed merge and
AssistX deployment, then authenticated GET and in-browser check. No unaudited
container restart or agent routing change was performed in this experiment.
Do not claim the new API or UI is live until that check passes.

The normal background NAS transfer admission continues to report HOLD during
high utilization, and root-only allocation accounting remains pending.
Sources remain retained, and no SSD bytes have been reclaimed.
