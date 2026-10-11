# AssistX Fleet inventory — authenticated Auto-Router cutover contract

**Status: draft implementation only, not deployed. October 11, 2026.**

## Why this change is required

The current running Auto-Router has GET /admin/providers and does **not**
have the new GET /admin/providers/effective (authenticated GET observed to
return 404). Auto-Router draft PR
[scottjoyner/auto-router#41](https://github.com/scottjoyner/auto-router/pull/41)
will protect inherited admin endpoints and introduce a sanitized in-memory
provider snapshot. AssistX Control Room already reads the raw legacy provider
catalog to populate the **Available** models column. It must neither display a
false blank Fleet view during the rollout nor treat this catalog as **Loaded**.

An authorized read-only GET **from the running assistx-api container** with
its pre-existing configured admin token returned HTTP 200 for both
/admin/providers and /admin/admission without printing the secret.
That verifies this one client has a working credential, not all clients.

## Read-only selection logic

- The default is GET /admin/providers/effective, authenticated with the
  configured AUTO_ROUTER_ADMIN_TOKEN header, with an isolated two-second
  request timeout supplied by the existing _http_json helper.
- If and **only if** that endpoint returns HTTP **404** (older router), fall
  back to an **authenticated** GET of the legacy /admin/providers.
- **Never downgrade** on HTTP 401/403, HTTP 429/5xx, a malformed effective
  body, an unexpected response schema, or ordinary network errors.
- The explicit AUTO_ROUTER_ADMIN_PROVIDERS_URL operator override is kept.
  An override to the legacy endpoint reads that schema directly and also
  requires a configured admin token; no override may trigger a second fetch.
- If the admin token is absent, show no catalog; **never issue an anonymous
  privileged GET** to compensate for missing credentials.
- Parse only bounded provider-model structures and identities. Effective
  snapshot v1 requires its explicit read-only source/schema markers, disabled
  providers are excluded, network identity uses hostnames only (not URL
  userinfo, query, or path). Model-ID spelling is preserved.
- The result may populate **Available**. Independent live graph evidence is
  still necessary for **Loaded**. The provider snapshot, whether current or
  expired, never confirms healthy/resident instances or permits dispatch.
- No workflow changes router provider assignment, load/unload state, leased
  inference, file custody, Govee plugs, or power/network configuration.

The default adds one lightweight 404 GET per Fleet cache refresh while the
router is still old; after deployment of PR #41 it takes only the new snapshot.
Both calls are read-only. The Control Room already bounds its Fleet cache to
10 seconds.

## Rollout order and gates

| Gate | Acceptance | Status |
| --- | --- | --- |
| Client regression | AssistX standalone parser, stdlib tests on Lenovo, Control Room page tests | **PASS locally; draft PR** |
| Legacy caller auth | Running AssistX API can GET old admin endpoints with configured token | **PASS for AssistX only** |
| Inheritance auth | Auto-Router PR #41 unit + integration-smoke CI | **PASS in PR; not deployed** |
| Other caller audit | Every operator/browser/script client has authentication or is retired | **OPEN** |
| Deploy client | Merge/review AssistX patch, check UI Available/Loaded distinction | **OPEN** |
| Deploy router | Review/merge PR #41, coordinate release/rollback; anonymous legacy route 401 | **OPEN** |
| Physical provider cutover | Reconcile Destroyer listeners (K2 :1235, Ornith :1236), direct clients | **OPEN** |
| Lifeboat service | iPhone pinned HTTPS on independent power/network and battery endurance | **OPEN** |

**Rollback principle:** If the new route cannot be read, it must not be
replaced by anonymous legacy access. When the old router is removed, retire
the authenticated legacy fallback in a separately reviewed hardening change.
No automatic deployment of either service is authorized by this document.

## Reproduce on an idle local node without installing Python packages

The pure parser module uses only the Python standard library. Put
src/assistx/router_provider_inventory.py into a temporary assistx/ directory
and tests/test_router_provider_inventory_portable.py into tests/, then run:

    python3 -m unittest discover -s tests -p test_router_provider_inventory_portable.py -v

The parser/test executable on Lenovo does **not** need the full AssistX
runtime, Neo4j, Docker, npm, or pytest. For full integration run (in an
existing isolated Python environment):

    PYTHONPATH=src python3 -m pytest -q tests/test_control_room.py tests/test_router_provider_inventory_portable.py

Only synthetic fixtures are used in these tests; no request is sent to the
production router from the tests.

### October 11 Lenovo acceptance receipt

Lenovo reran the final standalone parser fixtures with Python 3.12:
**10 passed** without dependency installation or credentials. The source
and test SHA-256 values were independently checked against the committed
candidate files. Structured evidence lives in
docs/evidence/assistx-provider-inventory-lenovo-20261011.json.

Separately, on the orchestration node, the existing integrated Control
Room fixtures plus the portable suite reported **28 tests and 15
parameterized subtests passed**; Ruff passed for the touched source and
test files. No live inference was attempted.
