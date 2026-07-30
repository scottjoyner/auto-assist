# Migration state ledger — 2026-07-30

The reconciliation migration uses an operator-owned YAML ledger so a local agent
cannot convert incomplete observations into a claim that the fleet is ready.

Template:

```text
deploy/reconciliation/migration-state.example.yaml
```

Working copy:

```text
deploy/reconciliation/migration-state.yaml
```

The working copy must remain untracked, mode `0600`, and free of secret values.
It stores evidence paths, checksums, identities, outcomes, and approvals—not API
keys, passwords, private keys, raw prompts, or production data.

## Initialize

```bash
make reconciliation-init
```

This creates the shadow environment file and ledger when they do not already
exist. Existing files are never overwritten.

## Update discipline

Update the ledger after each evidence-producing step:

1. baseline capture;
2. production restart-plan documentation;
3. Compose rendering and isolation review;
4. shadow service health;
5. strict-offline verification;
6. physical runtime/process inventory;
7. direct and routed completion canaries;
8. slot, queue, concurrency, and cancellation tests;
9. state-authority and restart/rebuild tests;
10. rollback rehearsal;
11. operator-approved Hermes synthetic task;
12. production Neo4j backup and restore-plan review;
13. cutover and rollback command review.

A failed or inaccessible command stays `fail`, `blocked`, `unknown`, or
`not_run`. Never mark it `pass` because the expected state seems likely.

## Runtime records

Create one record for each **physical loaded model instance**, not each access
URL. A Link-exposed localhost path and a direct Tailnet URL may refer to the same
runtime instance and therefore belong in one identity record.

An admitted runtime requires:

- physical runtime and observer node IDs;
- access URL and transport;
- runtime and model instance IDs;
- exact model key;
- loaded state and load owner;
- explicit integer `parallel_slots >= 1`;
- fresh observation and expiry timestamps;
- official observation source;
- passing completion, sequential stability, concurrency, and cancellation probes;
- no quarantine reason.

Unknown capacity is not represented as one slot. Keep the runtime unadmitted.

## Shadow readiness validator

```bash
make reconciliation-state-validate
```

The validator blocks until the ledger records:

- a captured and reviewed baseline;
- exact old-stack restart commands;
- `public_inference_found: false`;
- isolated and healthy shadow services;
- passing AssistX/router CI;
- passing Compose render and strict-offline checks;
- direct and routed completion evidence;
- runtime identity, slot, cancellation, state-authority, restart, and rollback evidence;
- at least one fully admitted runtime;
- an empty blocker list.

A passing result means the shadow evidence is internally complete. It does not
authorize Hermes or production changes.

## Production cutover validator

```bash
make reconciliation-cutover-gate
```

This adds requirements for:

- a passing synthetic Hermes task lifecycle;
- a production Neo4j backup;
- named and timestamped production-cutover approval;
- `cutover.recommended: true`;
- recorded client switch, old-stack restart, and rollback-threshold plans;
- a completed rollback rehearsal.

A passing result means the operator has enough recorded evidence to review the
exact maintenance commands. The local agent must still stop and wait for an
explicit instruction to execute cutover.

## Checksums and revisions

The final report must include:

- ledger checksum;
- every repository commit SHA;
- rendered Compose checksums;
- baseline evidence manifest checksum;
- backup checksum;
- runtime probe and benchmark evidence paths;
- exact operator approvals and timestamps.

Changing evidence after validation requires updating its checksum and rerunning
the validator.

## Failure handling

When validation fails:

1. preserve the output;
2. add concrete unresolved items to `blockers`;
3. do not weaken the validator to make the current state pass;
4. fix the underlying evidence or runtime condition;
5. rerun the relevant canary;
6. update the ledger and validate again.

`STATUS: BLOCKED` is an acceptable and often correct migration outcome.
