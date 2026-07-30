# AssistX current status

This page describes the repository at the current mainline checkpoint. The
dated [`STATUS.md`](STATUS.md) remains available as the earlier Paperclip
cutover record.

## Operational capability

AssistX now has a durable graph-backed control plane for:

- fleet inventory, real loaded-model visibility, and node health;
- opportunity-cost-aware task allocation and atomic reservations;
- claim leases, heartbeats, completion fencing, and stale-claim recovery;
- controller leadership with durable checkpoints and fencing tokens;
- signed typed recovery, verification, rollback, maintenance, and quarantine;
- checkpointed preemption and bounded task migration;
- bounded repository improvement with isolated Git worktrees;
- signed executor evidence, central acceptance, skill profiles, and
  review-first repair proposals;
- exact-fingerprint operator promotion with safe rollback;
- authenticated monitoring and controls in `/operations`.
- opaque prompt-prefix KV-cache manifests, strict model/quant/runtime
  compatibility, affinity-aware allocation, TTL/eviction, and reuse telemetry;

The real-Neo4j recovery canary exercises the lifecycle across recovery,
reservation ownership, checkpoint migration, improvement evidence, learning,
repair proposals, and promotion serialization.

## Authority and safety posture

- Neo4j is authoritative for task, lease, reservation, controller, recovery,
  migration, attempt, and audit state.
- A claim ID fences worker mutations. A superseded worker cannot heartbeat,
  checkpoint, or complete a newer execution attempt.
- Generic command payloads remain disabled unless
  `FLEET_UNSAFE_SHELL_TASKS_ENABLED=true`; production should leave this false.
- Recovery executes only signed typed runbooks through explicit service and
  Compose allowlists.
- Improvement workers operate only on configured repositories, allowed paths,
  allowlisted verification executables, and tier-specific file/diff budgets.
- Model output is not trusted as evidence. The executor measures Git state,
  runs verification without a shell, and signs the resulting envelope.
- An accepted improvement is still not deployed. An authenticated operator
  must approve the proposal and later promote the exact signed fingerprint.
- Promotion cannot commit, push, or open a pull request. It leaves a verified
  patch in the configured checkout for the normal release workflow.

See [`EXECUTION_AUTHORITY.md`](EXECUTION_AUTHORITY.md) for the full matrix.

## Deployment state

Recovery and self-improvement are opt-in. A node is not code-capable merely
because the API exposes the endpoints. Repository roots, writable isolated
worktree storage, node-specific signing identity, and control-plane
verification keys must be configured and mounted into the relevant process.

`GET /api/fleet/operations-readiness` reports recovery and optional
self-improvement prerequisites without returning secret values. Readiness is a
configuration gate, not proof that a live recovery or code-change canary has
passed.

The isolated [`end-to-end deployment`](end-to-end-deployment.md) bundle now
packages production-profile health, fenced task completion, cache telemetry,
cross-node migration, targeted bounded improvement, signed recovery health
checks, evidence capture, and state-preserving rollback into explicit stages.

Paperclip remains a supported execution backend. Direct Hermes execution is
also implemented. A deployment must select its intended execution authority
and must not run two consumers against the same eligible task population
without explicit reservation and idempotency controls.

## Remaining gaps

The next high-value work is operational rather than relaxing safety:

1. Run the end-to-end deployment stages on every code-capable node, including
   crash cleanup and signing-key rotation.
2. Export controller, reservation, migration, recovery, and improvement metrics
   to the production alerting stack.
3. Add operator-visible retention and cleanup policy for old evidence,
   checkpoints, patches, and abandoned worktrees.
4. Calibrate model opportunity-cost profiles from larger samples and expose
   confidence/age alongside each routing recommendation.
5. Add repository-specific verification policy presets without allowing a task
   author to broaden the global executable allowlist.
6. Continue the Operations UI overhaul with incident timelines, topology,
   accessible controls, and explicit degraded-state explanations.
7. Exercise disaster recovery for Neo4j state, controller leadership transfer,
   and partially applied external recovery operations.
8. Deploy and benchmark runtime-specific llama.cpp slot and SGLang HiCache
   adapters; LM Studio and vLLM remain affinity-first until their configured
   runtimes expose an approved portable restore boundary.

These gaps should strengthen observation, calibration, and recoverability. They
should not grant agents approval, promotion, release, or unrestricted shell
authority.
