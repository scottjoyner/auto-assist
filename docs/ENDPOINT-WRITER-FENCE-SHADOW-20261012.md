# Synthetic deny-only writer fence — NOT a live rollout

Stacked research companion to draft #336 and P0 issue #322.

**Nonproduction, unwired candidate.** `src/assistx/endpoint_writer_fence_shadow.py`
is not imported by running AssistX routes, registration writers, event
reconciliation, or the container configuration. The entire module rejects
any endpoint identity without the `scratch-acceptance-` prefix and
requires explicit synthetic rehearsal acknowledgements for policy, producer
inventory, queued-event drain and a positive generation. Its positive result
says only `ALLOW_*_REVIEW_ONLY`, never production authorization.

The six operation categories are node registration, node heartbeat, model
registration, model probe, model delete and event ingestion. Event ingestion
recognizes three legacy writer event kinds: `swarm.node.registered`,
`swarm.node.heartbeat`, and `model.endpoint.discovered`. The shadow
checks explicit and inferred legacy endpoint identities, detects contradictory
model/service IDs, recursively checks endpoint ID references in other event
payloads, and refuses malformed or unaudited policies. A synthetic callback
is not invoked for any HOLD or DENY outcome.

Physical isolated Neo4j acceptance extends the existing draft #336 harness.
After a synthetic relationship-preserving rekey, simulated stale node
registration, heartbeat, model probe and event replay callbacks are denied,
and the preserved model type and SERVES relationship are verified unchanged.
For contrast, the harness separately replays the actual *unfenced*
`swarm_core.py` legacy MERGE+SET statement and verifies semantic model
corruption remains possible despite uniqueness. The new canonical model
writer rejects that corruption. The disposable database and network are
removed, with no host ports, external credentials or live graph access.

## What this does NOT prove

- It does not fence a running HTTP route, worker, scheduler, retry queue or
  event bus. Live AssistX still contains the unsafe legacy source.
- It does not establish which remote producers actually send those events,
  ensure race-free gating, guarantee the enforcement configuration is
  centrally authoritative, or prove transactional durability across restarts.
- It does not reconcile consumer-visible endpoint ID aliases, cached
  discovery, SDK integrations, graph references, historical event envelopes
  or phone clients.
- It does not choose an approved replacement identity, change production
  Neo4j schema, or grant a new privilege or exposure.

**Next promotion gate:** independently inventory and authorize every ingress
path and producer, choose how to atomically reject/hold or drain pending
legacy registrations, verify ownership-bound identity binding and retry
semantics in staging, rehearse rollback, then implement one production
admission/fencing boundary in its owning codebase. Credential rotation
#156, graph network containment and schema/endpoint ownership approval
remain separate mandatory holds. A passing synthetic callback cannot waive
any of those gates.
