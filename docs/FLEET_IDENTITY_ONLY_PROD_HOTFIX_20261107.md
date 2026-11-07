# Additive API identity-only migration, exact deployed source baseline

**Scope:** two imported source modules and synthetic tests. No resource,
Task, approval, signer, model or graph mutation. Full task execution is HOLD.

## Physical preflight — November 7, 2026

- Active `assistx-api` container is healthy on loopback port 8000; its source
  mounts `/home/scott/git/auto-assist-deploy-20261016/src` writable.
- Exact deployed Git HEAD was
  `8f60b39215768519634521633d5ca0f492fa3704` (working tree clean).
- Active `src/assistx/api_router.py` SHA-256:
  `97ff6e031d816fd90d89f6cf28829990b2d66bb22cb647cdb6a32444d1a95261`.
- Active API `GET /health=200`, `GET /api/fleet-node/identity=404`,
  `GET /api/agent/tasks` without auth `=401`.
- Production token registry contains registered identities `x1-370` and
  `xwing` (names only observed). None of the five active remote workers has
  a machine identity or approved-origin variable in its running process.
- This source review **did not read or display** any real node-token values or
  move operator Basic credentials to nodes.

## Minimal correction

- `src/assistx/fleet_node_identity_only_route.py` uses the *existing* token
  verifier and registry to expose one identity-only GET. It requires both
  machine headers and replies only with `task_admission=HOLD` and
  `legacy_task_authorized=false`. A valid Basic credential is not machine
  identity. Invalid, partial and duplicate-key registry forms fail closed.
- The router also installs a fail-closed HTTP middleware: the five legacy
  Task GET/POST routes deny any machine header, including with valid Basic;
  operator Basic requests **without** machine headers remain unchanged.
- No new Task listing, claim, lease, completion, model or production runtime
  approval is provided by the hotfix.

## Independent evidence and rollout gate

- Exact deployed source-base offline TestClient: **27 passed**.
- Isolated HTTP Uvicorn canary without external network, database, prod
  credentials, ports or writable source: valid synthetic machine identity
  returned `200/HOLD`, Basic-only and invalid node returned `403`, and five
  Task routes with or without Basic were **10/10 denied** before graph access.
- Live deployed OpenAPI paths: **202**; test candidate **203**; exact additive
  path `/api/fleet-node/identity` only, with **zero removed** paths.
- A production release (NOT done by this document) needs a clean exact-head
  reviewed commit, independent production image/source custody, a protected
  rollback artifact, a controlled API restart and post-restart health `200`.
- Before real node identity testing, a designated credential owner must approve
  the existing `xwing` machine principal and an exact TLS/loopback origin; do
  not use Basic on the agent, print the token or copy it into a shared file.
- A positive identity-only test must **not** trigger any Task call. A separate
  fresh signer-approved runtime projection, resource-owned CAS/COMMIT and
  supervised positive-slot rollout are still needed for productive work.

## Rollback boundary

Protect the original production `api_router.py`, exact Git HEAD and service
image before cutover. Do not edit live bind-mounted code in-place without a
bounded, verified rollback path. On health, schema, or security regression,
restore the exact prior source, restart only the API, recheck existing health,
and leave Task admission and five worker services unchanged.
