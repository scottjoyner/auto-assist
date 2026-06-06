# AssistX Overlay Integration

## 1. Purpose

AssistX remains the canonical task-state and ingestion system. The overlay
layer adds adjacent services that participate in routing and assignment
without taking ownership of the AssistX data model.

The overlay currently consists of:

- `auto-router` for routing, quota, provider selection, and provenance
- `auto-assign` for assignment scheduling and task placement decisions

AssistX reports the overlay as a separate health slice so operators can tell
whether the core control plane is healthy on its own or healthy with the
routing/assignment layer attached. The overlay endpoints are exposed by the
`assistx.api_router` entrypoint so they are not duplicated on the direct-mode
API app.

## 2. Runtime Modes

- `direct`: AssistX runs with only its own core dependencies.
- `router`: AssistX expects `auto-router` to be present and healthy.
- `router_plus_assign`: AssistX expects both `auto-router` and `auto-assign`.

The overlay is optional in development and explicit in production. The
`compose.overlay.yml` stack switches the running entrypoint to
`assistx.api_router:app` so overlay routes are only registered once.

## 3. Environment Variables

- `ASSISTX_OVERLAY_MODE`
- `AUTO_ROUTER_BASE_URL`
- `AUTO_ASSIGN_BASE_URL`
- `AUTO_ROUTER_HEALTH_PATH`
- `AUTO_ASSIGN_HEALTH_PATH`

If overlay mode is not `direct`, missing required URLs are reported in
`GET /health` and `GET /api/overlay/status`, and startup validation fails
when the process is running in production mode.

## 4. Exposed AssistX Endpoints

- `GET /api/router/context-projection`
- `GET /api/router/backlog-candidates`
- `GET /api/router/status`
- `GET /api/overlay/status`
- `GET /api/overlay/endpoints`

These are read-only. They are used by overlay consumers to discover graph
context and to verify whether the overlay services are present.

## 5. Health Contract

`GET /health` now returns three layers:

- `configuration`: static runtime settings and whether overlay URLs are present
- `overlay`: status of `auto-router` and `auto-assign` when configured
- `dependencies`: Redis, Neo4j, and LLM reachability

Core health stays meaningful in direct mode. Overlay health only becomes
required when the overlay is enabled.

## 6. Operational Rule

AssistX does not treat the overlay as the source of truth for task state.
Routing and assignment services may read from AssistX and write back events,
but canonical state remains in AssistX/Neo4j.
