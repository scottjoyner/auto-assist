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

## Repository-wide CI status and isolated change gate

The source branch was cut directly from Auto-Assist main SHA
`a300072d11787a58a4587fa325f1b8fd66803730`.
GitHub [main CI run 37571131813](https://github.com/scottjoyner/auto-assist/actions/runs/37571131813)
already shows **unit FAIL** and **recovery-canary FAIL** at that exact
unchanged SHA. Candidate [PR #288 CI run 38116230764](https://github.com/scottjoyner/auto-assist/actions/runs/38116230764)
has the same two failed jobs, with **77 failed, 681 passed** in unit,
concentrated in unrelated repository-source-binding and legacy runtime
contracts. Recovery-canary fails in repository source binding
(`RepositorySourceBinding.from_contract_payload` unavailable).
PR #288 did not edit repository source-binding, runtime or recovery code.
This is evidence of **a pre-existing red baseline**, not proof that all
candidate failures are identical to main; do not claim regression-free full
suite until an independent apples-to-apples test establishes that.

CI has an additional `provider-catalog-contract` job to run exactly
the pure standard-library suite against only the candidate parser/test in a
temporary directory, matching Lenovo's execution environment. It does not
alter or skip the existing unit/recovery jobs; those must eventually be
fixed and pass before release or branch protection changes. No pip
installation, fleet credentials or external API calls are needed for the
isolated job.

## Adjacent authenticated admin consumer: Fleet loadout report

A bounded source audit also found that AssistX's
`src/assistx/api.py::_fetch_auto_router_fleet_report` called the existing
`/admin/ops/summary` endpoint via generic `_fetch_json` **without** the router
admin token. That endpoint is already protected in the live deployment;
unauthed callers receive 401. This is a separate source of empty operator
Fleet loadout reports and a release incompatibility with any hardened router.

The candidate now delegates only that fixed read-only GET to the isolated
`src/assistx/router_admin_client.py` helper. The helper requires
`AUTO_ROUTER_ADMIN_TOKEN` and a valid HTTP(S) origin, sends the credential only
on that fixed endpoint, and **rejects all HTTP redirects without forwarding
the token**. Missing tokens generate a bounded, explicit disabled result;
HTTP denials or invalid JSON remain failures, with no unauthenticated retry.
Other generic `_fetch_json` consumers retain their original unprivileged
requests and cannot inherit the privileged header.

The existing `tests/test_migration_api.py` Fleet proxy fixture is updated to
assert the exact admin token header and `allow_redirects=False` on the GET.
The full API integration fixture requires the broader AssistX runtime and is
not claimed green locally while the repository-wide tests remain red.

The additional pure unittest suite `tests/test_router_admin_proxy_contract.py`
uses only synthetic mocked responses. The combined client suites **16 tests
PASS on Lenovo**, with no installed package additions, no live router call,
no credential transfer, and no inference. The SHA-pinned receipt is
`docs/evidence/assistx-router-admin-client-lenovo-20261011.json`.
The existing isolated CI job now executes both pure suites, without weakening
or skipping full unit/recovery gates.

### Operator browser caveat

Auto-Router `ui_pages.py` has direct HTML links to `/admin/*` endpoints.
After PR #41, a plain browser click cannot supply `X-Admin-Token` and may
correctly receive **401**. Do **not** add admin tokens to query strings or
browser URLs to work around this: use an authenticated API client or a
separately reviewed session-aware operator interface. This UX acceptance
remains **OPEN** and is not silently certified by the AssistX parser tests.

## Frozen cross-repository producer/consumer witness

The real `effective_snapshot` function from Auto-Router PR #41 head
`0f5b416` was invoked **offline** with a synthetic provider in memory.
The resulting schema-1 JSON (not a handwritten payload) was accepted by
the actual AssistX PR #288 provider catalog parser. Configured-model
identities are preserved; loaded, service health and admission still
remain unverified. The frozen payload is in
`tests/fixtures/router_effective_snapshot_v1.json`.

`tests/test_router_effective_cross_repo_fixture.py` independently verifies
the producer checksum and consumer interpretation. It is included in the
stdlib CI isolation job and passed on Lenovo alongside the other tests:
**19 portable tests PASS**. Source and fixture digests are recorded in
`docs/evidence/assistx-cross-repo-pr41-producer-20261011.json`.
This is source-contract validation only, not deployed ASGI/HTTP evidence.

## October 11: cross-draft source-binding integration gate (PR #217 + PR #288)

Both exact current draft heads share main ancestor
`a300072d11787a58a4587fa325f1b8fd66803730`. An **uncommitted,
non-published disposable git merge** of AssistX PR #217 source-binding
head `42240ec5640c312ece54c50741c5c71fab4745b7` into this PR's
client-migration tested head
`0b48e60c1a1bc0c2c1d008c6f94b6c8298dec4f8` applied **without
conflicts**. It touched only PR #217's four source-contract files and
three scoped test files; no overlap with PR #288's provider/admin changes.

The selected `not integration` suite of seven test modules passed **86 tests,
38 subtests, 1 deliberately deselected** in a local Python environment:
source binding, repository task generation, improvement runtime, read-only
provider catalog, admin report client, producer-generated schema-v1
fixture and the Control Room. The excluded test
`test_repo_generator_does_not_start_when_disabled` can start a controller
loop and was also deliberately excluded in an earlier separate PR #217
worker-run acceptance; it needs its own isolated runner before counting
full-suite acceptance.

A wider local run could not collect every module because this host lacks the
`langgraph` package. Do not treat this as the actual GitHub CI root cause:
the hosted original main and PR #288 unit suites fail for additional
source-binding/runtime reasons. PR #217 separately has passing hosted
`recovery-canary` and failing hosted `unit`. None of those failing global
gates has been suppressed, waived, changed or promoted.

To make this compatibility experiment reproducible in a GitHub runner with
declared dependencies, `.github/workflows/router-auth-sourcebinding-composition.yml`
checks out **the exact client PR head**, fetches **only PR #217's exact
pinned head**, hard-fails on any head drift, applies the merge **without
committing it**, and runs the same seven-module selection with integration
tests excluded. It runs only for the named client-migration draft PR. A
green check means the pinned pair is compatible for this limited test,
**not** that either PR should be merged or deployed. The structured local
receipt and exclusions are in
`docs/evidence/assistx-pr217-pr288-uncommitted-composition-20261011.json`.

The safest order is independent review of PR #217's authority-preserving
source-binding changes, resolution of full AssistX unit/recovery baseline,
then review of PR #288 against the updated base. Neither PR is merged by the
composition workflow, and no live fleet services or storage are touched.
