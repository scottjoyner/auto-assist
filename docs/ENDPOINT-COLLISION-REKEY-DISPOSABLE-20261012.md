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
