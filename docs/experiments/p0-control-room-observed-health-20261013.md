# Control Room live dashboard contract and missing telemetry display — October 13, 2026

Draft-only PR stacked on #355. No production deployment, graph writes, model
autostart, credential/role/schema/topology changes, or approval renewal.

## Reconciled active route and template ownership

HTTP tests on x1-370 established that `/control-room` actually renders
`templates/base.html`, not the apparently enhanced child
`templates/control_room.html`. The latter extends a base that has no
overridable content block; editing it alone has no effect. The live
`/api/control-room/overview` emits **source-backed** dependencies,
runtime/process inventory, weighted model performance, recovery, power
and fleet-node observations. It does not promise `component_health`,
`trend_data`, `export_available` or `health_score` in the unrelated
`/api/fleet/dashboard` API. Those legacy tests were asking for invented
values; this change does **not** add synthetic numbers to that API.

## Implemented UI and truthfulness fixes

- Added observed-component and indicator panels to the *rendered base
  template* and bound the DOM classes consumed by control_room.js. Existing
  export and quick actions were already in base; the export label now
  warns that the user-triggered JSON contains internal fleet metadata.
- Empty doctor/dependency/runtime/power evidence reports UNKNOWN, not
  perfect `100` health or a bogus power healthy status. A Control Room
  overview with **no required dependency probes** now reports
  `overall_status=unknown`, while failed required dependencies are
  degraded and verified healthy required probes remain healthy.
- Removed invented `increasing` trend and `+1` deltas from one
  snapshot. Current observed fault counts remain visible, with trend
  marked unknown and explicit lack of history. Real historical trends
  require persisted time-series data and separate acceptance.
- Graph-sourced component names, issues, category and runtime values are
  HTML-escaped; status CSS classes use an allowlist. The empty UI states
  explicitly say UNKNOWN rather than clearing the panel to blank.

## Acceptance

- Revised `tests/test_enhanced_dashboard.py` verifies actual
  `build_overview` aggregates from deterministic synthetic source rows,
  degraded/healthy/unknown behavior, weighted throughput, source evidence
  absence, plus an **HTTP-rendered** minimal Control Room route.
- Updated two stale `test_fleet_dashboard_inference.py` HTML checks to
  assert live `/control-room` inference/runtime and source-backed sections
  rather than panels of a different, retired UI. Its two existing
  `/api/fleet/dashboard` active-inference tests remain unchanged.
- Local scoped `pytest` 28 tests pass, `node --check` passes, targeted
  Ruff passes. Full combined tests and exact SHA hosted CI are separately
  required after publication.

**This research does not claim production live measurements are healthy,
a historical trend exists, nor that the unit suite or P0 graph security is
fully cleared.** Keep B01/B02/B06 and auto-failover HOLD, and keep both
24-hour renewal timers disabled.
