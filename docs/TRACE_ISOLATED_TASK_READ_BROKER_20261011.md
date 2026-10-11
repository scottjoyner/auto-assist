# AssistX #148 — isolated task-read broker physical proof, October 11, 2026

**Research-only, stacked after draft PR #294. Production graph-admission/failover NO-GO.**

## Result

On x1-370, a **disposable three-container experiment passed twice** (47.31 and 57.70 seconds) using cached `neo4j:5.26-enterprise` and `python:3.12-slim` images. It migrated one **intentionally narrow** slice of the existing `Neo4jClient.get_task(task_id)` behavior behind an exclusive broker:

`worker (worker-only Docker bridge) → fixed-plan HTTP broker (two private bridges) → read-only Neo4j role (graph-only bridge)`.

All networks were `--internal`; there were **no published host ports or host bind mounts**, and no connection to any running production AssistX or Neo4j services. All containers/networks were removed in the `finally` block. Credentials and tokens were randomly generated fixture secrets, never committed.

**The worker's `TaskReadBrokerClient.get_task` method returned the synthetic Task's `id` and `status` through the broker**. The worker could not connect directly to the graph's Bolt **or** HTTP listener using Neo4j's literal container IP, but could reach the broker. No direct Neo4j credential was placed in the worker. The broker accepted exactly one source-reviewed static Cypher operation, and rejected missing bearer, arbitrary worker Cypher, injected parameters, unexpected response fields and unapproved plans.

**Actual Neo4j server RBAC was also exercised:** a disposable role with ACCESS and MATCH over `Task.id`/`Task.status` permitted the synthetic task read but denied `CREATE (:Task ...)`. This tests real server authorization, not merely code filters.

## Scope and fidelity

Existing source:
- `src/assistx/neo4j_client.py::Neo4jClient.get_task` executes `MATCH (t:Task {id:$id}) RETURN t` and returns **all accessible Task properties**.
- `src/assistx/api.py::api_claim_task`, task heartbeat/completion and review workflows call `neo.get_task`. Claim controls inspect `payload_json`, `queue_class`, `required_capabilities`, `payload`, and `runbook`. Those fields are **not** represented in the two-field broker fixture. Replacing the current implementation with this slice would introduce authorization and workflow-control regressions.
- The broker intentionally returns only `Task.id` and `Task.status`; this is a **compatibility adapter proof**, not production API or complete data parity.

The prototype's graph HTTP path is internally restricted and uses Neo4j Basic credentials; it has no external TLS or independent client authentication beyond a fixture bearer token. This is adequate only inside the disposable isolated internal network. A production design needs mutually authenticated service identity and strict data classification.

## Files and repro

- `tests/trace_disposable_task_broker.py`: standard-library HTTP broker, static Cypher, exact parameter shape and narrow response validation.
- `tests/trace_task_read_broker_client_research.py`: worker-side `get_task`-shaped shim; no automatic fallback to Bolt on broker failure.
- `tests/trace_disposable_task_worker.py`: runs physically from an isolated worker container; reads synthetic Task, denies injected plans/auth and literal-IP Bolt/HTTP.
- `tests/probe_trace_isolated_task_broker.py`: manual-only Docker topology, ephemeral Neo4j role+user, seed synthetic Task, verify read/write separation, start broker+worker, validate and clean up.
- `tests/test_trace_isolated_task_broker.py` and `tests/test_trace_task_read_broker_client.py`: 36 offline tests of exact plan, error boundaries, response validation, origin refusal, and isolation source guards.
- `.github/workflows/trace-graph-entry-research.yml`: runs those offline tests and compiles physical probe; never runs Docker/physical experiment on GitHub Actions.

Manual opt-in on x1-370, with cached images:
```bash
PYTHONPATH=tests ASSISTX_DISPOSABLE_GRAPH_BROKER=1 \
python3 tests/probe_trace_isolated_task_broker.py
```
The research harness prints a sanitized JSON result. Source and results: [evidence](../research/evidence/assistx-isolated-task-read-broker-20261011.json).

## Concrete remaining migration plan

**G0 (next)** Inventory every real `get_task` field read across claim, recovery, dispatch and review paths. Define explicitly filtered projections **per consumer**, preserving `required_capabilities`, `payload_json`, `queue_class` and all other policy-relevant properties where needed. Establish full **differential fixture parity** between existing `Neo4jClient.get_task` and broker result; do not blindly return every property or hide security-critical fields.

**G1** Place the exclusive broker's network and database credentials in a dedicated OS/container principal. Workers receive only an authenticated RPC identity; broker alone receives server-side minimal graph role. Remove all direct graph credentials from workers (after feature parity) and prove direct worker Bolt/HTTP failures from each network namespace.

**G2** Wire the **actual** quorum-authenticated admission, immutable journal-before-graph, signed independent witness, generation-aware physical closure and stable authenticated request identities from PRs #249–#275 **into this broker**. This proof did **not** implement Raft admission or transaction witness integration.

**G3** Only after zero functional regression and reviewed rollback: eliminate Neo4j's current worker/API shared bridge attachments, wildcard host ports 7688/7475, and independently inspect Tailscale Serve TCP 7687 as alternate entry. Re-test every live fleet node, not just a disposable worker. A live network cutover is a separate operator-approved change.

**G4** Verify actual production SSO header provenance and API/worker trust boundaries, then run 1/3/5/10 authenticated clients with p95/p99, cancellation, restart, quorum outage and disk/quota/snapshot recovery. Do not mistake research server role denial for server-enforced generation fencing.

## No-go claims to retain

- Current x1-370 **production** `assistx-api`/`assistx-worker` still have direct Neo4j network paths and configured graph credential variable *names*, as established in [draft PR #294](https://github.com/scottjoyner/auto-assist/pull/294). This experiment **did not change them**.
- Broker prototype is **read only**, does not preserve full Task properties or production claim/review invariants, and does not consume Raft permits.
- Local host/Docker administrator retains privileged access to internal bridge networks; distinct trusted host failure domains and credential custody remain unproven.
- This canary is not a production security control or approved release.

**Disposition: physical proof of concept PASS; production graph admission and automatic failover NO-GO; keep issue #148 open, PR draft-only.**
