# B02 tenant metadata-only read — disposable acceptance (2026-10-13)

**Classification:** research-only, no route mounted, no live graph query, no
network / proxy / Tailscale / Docker production change. Source base is B02
updated PR #342 research checkout at `a254f765192c06dbf73f756bc6c01a86103db830`.
Original PR #294 physical B01 network bypass remains **NO-GO**.

## Scope

New plan `harness_evolution_tasks_tenant_metadata_v0` is intentionally distinct
from the legacy status-only plan. It uses a fixed, parameterized Neo4j Cypher
predicate on **both tenant_id and status**, bounded to exactly 40 results.
The response explicitly reconstructs the legacy nine-field shape with
`title`, `summary`, `last_error`, and `payload_json` all `null`.
Those fields are not requested from Neo4j. This is **not view parity**:
objective, mistake and chain details are intentionally unavailable.

A synthetic Basic principal is matched to a fixture-only server-side tenant
map. Headers are ignored. This is an independent test fixture, **not** a
second approved authentication implementation: any future integration must
reuse and independently validate PR #342's `trace_b02_tenant_broker_boundary`
principal binding, rather than running two competing identity policies.
Neither fixture establishes real Task.tenant_id provenance or ownership.
## Demonstrated negatives (disposable only)

- Wrong Basic credentials, forged principal, missing/invalid tenant binding,
  unapproved status/limit all fail closed in pure tests.
- Any returned foreign, unattributed, enriched, status-mismatched, or
  malformed row fails the entire response instead of filtering silently.
- The fixed Cypher query returns no free-text or payload properties.
- The read-only Neo4j Enterprise role denies synthetic writes.
- A synthetic reader with the same role can still issue **arbitrary**
  unscoped Cypher to read the other tenant. This is an expected negative.
  Consequently RBAC alone is not an isolation boundary: all arbitrary Cypher
  access must be denied outside the fixed broker query.
- The separate disposable broker-network probe tests worker denial to
  Bolt and graph HTTP by literal graph IP, successful broker lookup,
  missing bearer denial and arbitrary-plan denial.
- The probes use uniquely labeled, internal-only Docker networks, no host
  published graph ports, no bind mounts and clean up fixture containers.

## Reconciliation with current B02 live observations

The latest #342 evidence records **31,074 Task nodes** but **zero** Tasks
matching the five harness-evolution kinds. It also reports no `tenant_id`,
`owner_user`, or `created_by` property keys in the bounded read-only
schema preflight. This new tenant metadata query is therefore a
**synthetic design candidate only**, not an appropriate live harness
migration: it could silently drop unowned legacy Tasks and adds no
proven benefit for the current zero-match Task workload. The existing
aggregate-only canary remains the narrower live observation surface.

## Gate still not satisfied

1. Establish source-authoritative tenant ownership metadata (or equivalent
   immutable ownership rule) for real Task nodes. Missing ownership must deny.
2. Bind real caller identity to a tenant using a reviewed authentication
   authority. The synthetic credential fixture is insufficient.
3. Prove the broker exposes **only** approved fixed queries; no arbitrary
   Cypher/Neo4jClient interface, write grant, global-operator bypass or
   cross-tenant fallback.
4. Reconcile control-room view compatibility explicitly. This minimal
   projection sacrifices title, summary, errors, endpoint, fail-set, chain
   and suite details. Any higher-fidelity release requires separately
   reviewed typed fields, PII classification and parity tests.
5. Show authenticated ingress proxy-header provenance and physical denial
   of direct Bolt from both assistx-api and assistx-worker, including
   hostname, raw Docker IP, host-published address and Tailnet paths.
6. Pin exact PR head, obtain independent code/security review, and gate all
   network/graph/credential changes under an explicit separate approval.

**Do not** merge, deploy, change containers/network/ports, rotate secrets,
run production Cypher, enable admission/failover, or promote Task read paths
on these results alone. B01/B02/B06 retain HOLD.