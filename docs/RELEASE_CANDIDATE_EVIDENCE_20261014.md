# AssistX / Auto-Assist integration release evidence — 2026-10-14

> **Status:** DRAFT; CONTROL PLANE RELEASE HOLD. **Scope:** deployed-tree source custody, catalog, admission, graph, tracing and iOS integration. **Source commit:** current GitHub main 6824a55fa89e0670c402d289421e44a15ef927ae (local original checkout at 567ea82a63894c0340d44d23c853500d47fafa1b is stale). **Last verified:** 2026-10-14. **Owner/reader:** AssistX security/backend operator and independent reviewer. **Evidence links:** [cross-repo release hub](https://github.com/scottjoyner/knowledge/blob/docs/release-evidence-20261014/20-Projects/auto-suite/README.md), [#338](https://github.com/scottjoyner/auto-assist/pull/338), [#294](https://github.com/scottjoyner/auto-assist/pull/294), [#366](https://github.com/scottjoyner/auto-assist/pull/366). **Known gaps:** exact production SHA, rotation, graph direct access removal, resource-owned replay proof.

## Source versus merged versus deployed

- **Observed in current GitHub main:** authenticated mobile runtime catalog implementation at src/assistx/mobile_agent_routes.py:637-669 and mobile chat from line 666 onward; runtime_projection.py and runtime_projection_v2.py exist. No live service request was sent.
- **Merged history:** PR #338 reconciles the deployed-source branch with a pinnable source line, but this does not attest a restarted/deployed process, credential custody, or env history. Issue #156 remains a separate operator-owned decision.
- **Open research drafts:** PRs #361/#365 (deny-only signed routing and malformed shadow receipt handling); #363/#366 (remote witness, uncertain PENDING, co-restore NO-GO); #294 (Neo4j direct Bolt/proxy identity). Their code is **not assumed in main** or deployed.
- **CI-at-head only:** #366 head b7c7074a42817c36e41183b6cbdeb0339b879e9f has successful GitHub CI run 38573911892 and synthetic-only 38573911568. Neither is physical graph/resource readback.
- **B01/B02/B06 NO-GO** stays. Do not enable claim/failover, graph effects, model loading or production networking.

## Architecture/process boundaries

~~~mermaid
flowchart LR
  Device["Kipnerter mobile"] -->|"authenticated"| Gateway["mobile_agent_routes.py"]
  Gateway --> Projection["runtime_projection(_v2).py: admission view"]
  Gateway --> Router["Auto-Router: private /v1/chat/completions"]
  Router --> Local["Approved local runtime; residency separate"]
  Control["AssistX scheduling, leases, source graph"] -.-> Projection
  Graph["Neo4j direct access bypass still open"] -.-> Control
  Observer["System One / shadow observer"] -.->|"deny-only, not authority"| Control
~~~

Authority is separate from telemetry: catalog listings, LM Studio sightings and shadow receipts do not grant admission. A router or mobile UI must not silently reselect an unadmitted model. Data ownership and signing boundaries are detailed in [HIGH_LEVEL_DESIGN.md](HIGH_LEVEL_DESIGN.md) and [LOW_LEVEL_DESIGN.md](LOW_LEVEL_DESIGN.md).

## Mobile API truth table — local source only

| Route | Auth / response | Evidence |
|---|---|---|
| GET /api/v1/auth/whoami | Tailnet identity or legacy operator Basic path; legacy authenticated user is **not** represented as Tailscale SSO | src/assistx/mobile_agent_routes.py:605-635 |
| GET /api/v1/runtime/catalog | mobile_auth and Tailnet identity recheck; intended sanitized empty catalog on projection/signing failure and intended 503 on unexpected error, and **26/26 exact current-main focused offline tests PASS**. Historical stale local checkout 567ea82a6389 fails 2/22 from a missing RuntimeProjectionSigningError symbol; current main defines the alias at runtime_projection_v2.py:158 | src/assistx/mobile_agent_routes.py:637-669 |
| POST /api/v1/model/chat/completions | mobile_auth; model-handle resolution fails closed; projection unavailable 503, model not ready 409, router config unavailable 503; no-store; 5s connect/300s total source timeout | src/assistx/mobile_agent_routes.py:666-727 |
| GET /api/v1/runtime/observations | **NOT OBSERVED in the inspected source route search**; do not document as deployed. Search exact candidate head before asserting 404/200 or schema. | Local grep at source SHA only |

Catalog schema-1 compatibility vs schema-2 Ed25519 projection field binding, expiry and independent verifier trust roots must be reviewed at exact source and with negative test fixtures. Model handle and observed host-loaded model identities are not interchangeable. A 200/empty admitted array is a fail-closed state; current GitHub main focused tests pass but historical stale local tests raised ImportError. Do not infer deployment from source tests, **not** transport or operator admission success. A 401/403 is an auth decision; 503 a declared unavailable state.

## Graph, keys, witness and failover threat model

| Threat | Invariant for acceptance | Current gate |
|---|---|---|
| Source drift / env-history credential custody | Compare deployed module/compose fingerprint to pinned source without reading secret values; #156 rotation owner decides remediation before redeploy | HOLD |
| Neo4j direct Bolt writer/reader, forged proxy header | All legitimate callers via least-privilege broker with verified identity and independent direct denial | #294 NO-GO; seven claimed paths must be independently re-censused |
| Two concurrent authorities or stale-effect term | Exactly one accepted resource term; stale writer cannot take effects after ownership change | B01/B02/B06 NO-GO |
| False signed post-COMMIT, co-restored graph+SQLite | Independent resource-owned cryptographic oracle and monotonic high-water outlive copied/rolled-back state | #363/#366 NO-GO |
| Witness PENDING despite uncertain graph COMMIT | Quarantine, never retry/compensate based on snapshot resemblance alone | NO-GO |
| System One malformed shadow receipt | Fail closed and distinguish a proposed persistence hook from a deployed persistent sink | #365 draft-only |
| Prompt/body trace exfiltration | Metadata-only defaults, authenticated pages, bounded pagination, no-cache and redacted previews | Source controls require specific head/fixture review |

## Safe diagnosis runbook (decision only)

1. Compare source commit and deployed runtime identity **read-only**; if mismatch, label DRIFT; do not sync or restart.
2. If catalog empty, distinguish invalid identity, expired projection, missing signing, zero admitted models and separately observed residency. Do not load any model.
3. If PENDING/COMMIT inconsistent, capture sanitized pair of source/witness statuses and stop; no auto retry.
4. If direct Neo4j access remains, keep security NO-GO; propose a caller-by-caller broker migration with non-bypass negative tests before production ACL changes.
5. If token/credential compromise suspected, stop documentation of values and escalate owner-led rotation #156; no secret-bearing examples.
6. Rollback proposal must identify pre-change pinned deployed process, compatible schema and durable audit/nonce custody; never revert high-water state to clear a failure.

## Reproducible docs-only evidence commands

~~~bash
git rev-parse main
git grep -n '"/api/v1/runtime/catalog"' -- src/assistx/mobile_agent_routes.py
git grep -n '"/api/v1/model/chat/completions"' -- src/assistx/mobile_agent_routes.py
git grep -n '"/api/v1/runtime/observations"' -- src/assistx || true
# Inspect the exact GitHub PR head + Checks; never run a production workflow.
~~~

Focused offline TestClient tests **executed twice, distinguishing stale original checkout and current GitHub main**: `env PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src python3 -B -m pytest -q -p no:cacheprovider tests/test_mobile_runtime_catalog.py tests/test_mobile_agent_routes.py` → stale local checkout **20 passed, 2 failed**; isolated current GitHub main `6824a55fa89e` **26 passed, 0 failed** (1 deprecation warning). See central [reconciliation](https://github.com/scottjoyner/knowledge/blob/docs/release-evidence-20261014/20-Projects/auto-suite/2026-10-14-CATALOG-NEGATIVE-TEST.md)). **Not executed for this documentation effort**: production API, graph integration, rotated credentials, physical router access, or release CI dispatch.

## Release note / owner checklist (UNRELEASED)

- **Source:** authenticated runtime catalog with **passing current GitHub main focused error-path tests**, model-handle-gated chat, and strict separation between control authority and observed residency.
- **Draft only:** independent graph witness, co-restore classifier, deny-only signed routing, shadow receipt, exclusive graph broker.
- **Upgrade order proposal:** credential/source custody → graph auth/fencing resource oracle → router delivery attestation → iOS schema/auth compatibility → protected phone restore → signed physical 1.7 device QA. Each step requires independent approval.
- **Signoff:** backend owner, graph/security reviewer, router owner, iOS release/device owner, independent release owner — all **PENDING**. Old design docs must not be read as contemporary deployment attestation.
