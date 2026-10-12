# Disposable, nondestructive endpoint identity disambiguation

**RESEARCH ONLY — not a migration or production-data cleanup.** Stacked on
draft PR #324, this change extends its existing guarded synthetic Neo4j harness.
No app path imports the scenario, no production Neo4j address or credential
is accepted, and the harness uses its own Docker network with no host port or
production mount. Default invocation without `--run-disposable` returns HOLD.

The specific physical defect in #322 is two nodes sharing one
`ServiceEndpoint.endpoint_id`: a generic service and a
`ModelEndpoint:ServiceEndpoint`, owned by the same SwarmNode, with different
properties and EXPOSES/SERVES relationships. Blind deletion, DETACH DELETE,
or retyping one node would lose information. Blind constraint DDL fails.

This synthetic-only candidate tests an **explicitly approved rekey of the
ordinary service's identity**, leaving its node, attributes and EXPOSES edge
intact. The model node retains its existing endpoint/model ID and SERVES edge.
The transaction refuses anything other than exactly one generic service node
and one model node of a single known owner with the expected relationship
counts, and refuses an occupied target ID, foreign owner, or missing approval.
A full DB-wide uniqueness constraint is installed *only on the disposable
graph after the synthetic conflict is resolved*. Existing canonical writers
are exercised on both nodes.

**These are not production-cutover semantics.** The appropriate new identity
for a live generic service is an unresolved owner/consumer decision; existing
external references, dependent endpoints, event ingestion, historical trace
keys, retry semantics, and stale writers may depend on the original identity.
A production migration needs the issuer/custodian, graph owner, consumers,
source-of-truth and rollback approval; an atomic quiesce of all legacy writers;
a snapshot/checkpoint and forensic audit; source/event compatibility;
independently verified Neo4j privileges; staged negative tests; and dual-stack
ingress and credential custody issue closure.

The synthetic test proves graph behavior, not authorizes live repair.
No production CREATE/SET/DELETE, schema DDL, roles, credentials, models,
Tailscale routes, iPhone release, or NAS actions are performed.

## Mandatory stale-writer negative: uniqueness does not prevent semantic corruption

The actual `swarm_core.py` legacy ordinary-service writer still uses
`MERGE (e:ServiceEndpoint {endpoint_id:$endpoint_id})` followed by an
unrestricted `SET e += $props`. After the synthetic ordinary-service identity
is rekeyed, the *old* endpoint ID remains on the model endpoint. If a queued,
late, retried or concurrent legacy generic-service registration uses the old
ID, its MERGE targets the **model** endpoint and changes its
`service_type` to `generic`. The uniqueness constraint still holds: this
is a semantic conflict, not a duplicate-key conflict.

The disposable harness intentionally replays that stale legacy statement as
a **negative** at the end of all successful canonical-writer tests. It must
observe synthetic model-kind corruption while retaining EXPOSES/SERVES
relationships; the unwired canonical model writer must then reject that bad
state. This is never a viable runtime operation. It is an adversarial proof
that simply fixing the duplicate nodes and adding uniqueness is **insufficient**.

**Non-waivable migration sequence:** inventory all producers/queues/retries
that use the old endpoint ID; freeze or fence both legacy registrations; drain
and reconcile in-flight messages; approve consumer-visible ID remapping and
rollback; repair data in a reviewed transaction; enforce uniqueness; then
enable the revised canonical writers. Independently validate runtime role
permissions, service/model event semantics and sustained absence of late
legacy registrations. If any writer still has authority to send the old ID,
the cutover must remain NO-GO.
