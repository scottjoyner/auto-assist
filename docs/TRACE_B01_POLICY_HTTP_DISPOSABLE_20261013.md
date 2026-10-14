# B01 — disposable Task policy classification over a real HTTP broker

**Date:** 2026-10-13. **Posture:** Research only, draft, production NO-GO.
Stacked on [draft PR #349](https://github.com/scottjoyner/auto-assist/pull/349)
at `8d8d3da39d18b65d87ed0e34e2c582e7492fdba4`.
No live AssistX route was mounted; no real Neo4j, admission, credentials,
network configuration or topology was modified.

## Motivation

PR #349 demonstrated a security-significant counterexample: replacing the
full `Neo4jClient.get_task` with a two-field read silently causes the real
API helper `_is_recovery_task` to misclassify recovery work as ordinary.
That would risk skipping the *conditional* recovery-node identity gate
if such a substitution were wired into the current API. PR #349's pure
classification contract passed offline tests but was not exercised
through the disposable broker's HTTP request path.

## Added isolated HTTP plan

The opt-in, isolated test broker now has a third **fixed** plan:
`get_task_policy_classification`. It uses the existing fixed six-field
claim-guard Cypher, not a caller-supplied query. Requests must have
exactly `plan_id` and `parameters={id,agent_id}`. Both names are
restricted to synthetic test identifiers. Arbitrary Cypher, role,
database URI, tenant and query-parameter injection are refused.
In addition to the existing random research-only broker bearer, this
fixed plan requires a **separate, random synthetic node token** and
agent identifier that match a server-owned disposable fixture. The
broker rejects a missing/wrong node token, mismatched agent ID or
missing bearer **before reaching Neo4j**. This enforces node identity
for *every* policy read and is intentionally stricter than the
legacy application's conditional recovery-node token check.

The broker extracts only the reviewed synthetic policy projection,
runs the three exact checked-in pure API classification helpers
under the *synthetic* drain-mode fixture, and responds only with
schema, id, status, `requires_recovery_node_identity`,
`drain_mode_policy_candidate` and **false** values for
claim, resource-fence and release authorization. It never returns
runbooks, raw payloads or capability lists on this plan.

This is **not** an independently trusted production identity issuer:
the bearer, agent ID and node token all come from one generated
fixture config. There is no binding to a real fleet device,
Task ownership, sealed key custody, persistent or replay-resistant
admission proof, audited authorization or real mTLS.
## Physical acceptance and limitations

A disposable `neo4j:5.26-enterprise` instance, private graph bridge,
dual-attached broker and worker plus six plain-Python simulated peers
were launched under existing opt-in
`ASSISTX_DISPOSABLE_GRAPH_BROKER=1`.
Graph has zero host-published ports and no production attachments.

The worker's real HTTP calls proved:
- Valid fixture bearer+agent+node token accepted for a synthetic recovery
  Task, returning the required recovery classification with no raw payload.
- Missing or incorrect node token and mismatched agent denied HTTP 403;
  missing bearer denied HTTP 401; injected policy parameter denied 422.
- Unknown fixture Task returned a null policy; synthetic critical Task
  was recognized as critical in the source-pinned drain policy.
- A separately seeded Task with malformed policy JSON returned HTTP 503
  `POLICY_OR_GRAPH_UNAVAILABLE` rather than a permissive classification,
  with no raw graph record or malformed input echoed to the caller.
- Read-only graph role prevented writes; arbitrary plan and injected
  Cypher were refused; seven synthetic peer roles still could not
  access direct graph IP/DNS Bolt/HTTP.
- No claim or graph mutation endpoint exists in the research broker.
  Successful classification does **not** assert a claim grant.

**Initial physical run:** PASS, exit 0, 60.47s command runtime;
`policy_http_classification_verified=true`. Disposable fixture only.
## Remaining independent acceptance gates

1. Production issuer/agent identity provenance, task ownership, explicit
   per-task caller authorization, signed nonreplayable attestations.
2. Real Task identifier and payload-shape census; error/nullable
   classification parity, malicious/ambiguous source treatment.
3. Do not rely on read-time classification for state changes. Atomic
   resource-side claim/heartbeat/complete transaction preconditions
   must verify term/high-water ownership, status and policy.
4. Real service-image parity, complete graph consumer migration,
   seven-peer production Bolt/HTTP denial, host-port and Tailnet-path
   closure, independently reviewed ingress header source.
5. Exact GitHub-head CI, physical verification, independent code/security
   review, staged rollback and explicit separate production approval.

**B01/B02/B06 remain HOLD. This draft authorizes no merge, deploy,
Neo4j schema/data change, credential rotation, network modification,
or production admission/failover promotion.**