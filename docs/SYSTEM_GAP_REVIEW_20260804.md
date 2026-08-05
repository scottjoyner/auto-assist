# Cross-Repository System Gap Review — 2026-08-04

## Status

This review covers the current default branches after the canonical HLD and LLD documents were merged for:

- `auto-assist`
- `auto-router`
- `lms`
- `fleet-llm-profiles`

The repository architecture is substantially defined and the major fail-closed contracts are implemented. The remaining gaps are primarily cross-repository integration, physical proof, production operations, and completing the evidence-to-admission lifecycle.

No physical node, runtime, production database, route, profile admission, firewall, or Tailscale configuration was changed by this review.

## Executive assessment

The system currently has four strong but deliberately separate layers:

```text
lms
  -> signed non-admitting observation, qualification, canary, soak, rollback evidence

fleet-llm-profiles
  -> independently verified desired-state profile with admission.enabled=false

auto-assist
  -> durable identity, allocation, reservation, claim, recovery, and admission authority

auto-router
  -> strict-offline projection consumer, priority-aware admission gate, path selection, forwarding
```

The principal system gap is the controlled handoff between these layers. Each repository correctly refuses to infer the authority of the next stage, but the complete operator-reviewed promotion protocol has not yet been implemented and physically proven end to end.

## P0 — release-blocking system gaps

### P0.1 First signed physical acceptance sequence has not run

Repository CI proves software behavior, not the fleet. One noncritical node still needs the complete sequence:

1. signed fleet observation;
2. signed exact-loadout qualification;
3. transactional canary with sustained soak;
4. verified rollback to the previous runtime;
5. negative failure drills;
6. import into a real profile that remains non-admitted;
7. independent, time-bounded live admission.

The active execution checklist is `scottjoyner/lms#7`. Until this succeeds, no runtime should be considered production-qualified solely because the code and schemas are merged.

**Acceptance criteria**

- one reviewed node/loadout completes all positive stages;
- rollback health is independently verified;
- required negative drills produce the expected failed evidence and recovery behavior;
- one real profile is imported with `admission.enabled=false`;
- any live admission is explicit, external, expiring, and revocable.

### P0.2 No canonical profile-to-admission promotion contract

`fleet-llm-profiles` intentionally stops at desired state. AssistX owns live admission, but there is not yet one canonical machine-readable promotion object that binds:

- profile ID and revision;
- physical node/runtime/model identities;
- exact loadout fingerprint;
- observation, qualification, and canary fingerprints;
- approved access paths and one shared slot pool;
- freshness limits and evidence expiry;
- signer identities and revocation state;
- rollback profile and current rollback-health proof;
- operator approval, admission scope, expiry, and revocation reason.

A Git commit or `admission.enabled=true` field must never become the admission mechanism.

**Required design**

Introduce an AssistX-owned `RuntimeAdmissionCandidate` and signed/approved `RuntimeAdmissionLease` lifecycle:

```text
verified disabled profile
  -> admission candidate
  -> current live revalidation
  -> operator approval of exact fingerprint
  -> expiring admission lease
  -> runtime projection generation
  -> continuous freshness/rollback monitoring
  -> expiry or revocation
```

### P0.3 Router does not yet enforce the complete AssistX authority contract

The router still documents two missing controls:

1. consume approved multi-path runtime observations directly from AssistX rather than relying on a reconciliation provider file;
2. require an AssistX reservation or signed route decision for workloads that need durable assignment semantics.

The runtime projection is already fail-closed, but the cross-service contract must become the only production source for admitted runtime/model/path/capacity state.

**Acceptance criteria**

- production providers are generated from a signed or authenticated AssistX projection;
- each projection has generation, revision, issued/expiry times, checksum/signature, and rollback identity;
- durable tasks carry a current reservation or signed route authorization;
- stale, revoked, mismatched, or unsigned projections and route decisions are rejected;
- aliases and multiple access URLs cannot duplicate physical capacity.

### P0.4 Physical LAN/Tailscale and shared-slot behavior is unproven

The code models one runtime with several private paths, but the following must be demonstrated from the deployed router container and under concurrent use:

- LAN-first path selection;
- explicit Tailscale fallback;
- recovery when the preferred path returns;
- no hidden public or unapproved fallback;
- one shared slot pool across loopback, LAN, Tailscale, and LM Studio Link views;
- cancellation-safe lease release during path failure;
- correct queue priority under real streaming load.

**Acceptance criteria**

A signed evidence bundle records path probes, selected transport, runtime identity, concurrent admission behavior, and no capacity double-counting.

### P0.5 Degraded-control-plane and durable-state recovery need a physical rehearsal

The repository implements fenced degraded activation, bounded hot state, hash-chained operational journaling, Neo4j replay, and leadership relinquishment. The following remain physical gates:

- isolated restore of current Neo4j backups;
- standby installation and inert `423`/zero-capacity proof;
- signed monotonic activation with witness or break-glass fence;
- heartbeat-qualified delegation to surviving runtimes;
- `PENDING_DURABLE_COMMIT` behavior during Neo4j loss;
- exactly-once journal replay after Neo4j return;
- worker promotion and one fenced synthetic task;
- Hermes promotion only after the synthetic lifecycle passes;
- primary return to `RELINQUISHED` with zero remaining journal entries;
- rollback, failed-health containment, and evidence preservation.

**Acceptance criteria**

A complete rehearsal evidence manifest passes the implementer-handoff validator and independently demonstrates the state transition sequence on the intended recovery hardware.

### P0.6 Cross-repository compatibility is not gated by one integration workflow

Each repository validates its own code and schemas, but no single workflow currently proves that the latest default branches remain mutually compatible across:

```text
lms evidence output
  -> fleet profile importer/schema chain
  -> AssistX admission candidate/projection schema
  -> auto-router projection and route authorization consumer
```

This creates a drift risk even when all four repositories are individually green.

**Acceptance criteria**

Create a pinned cross-repository contract workflow that:

- checks out exact reviewed commits from all four repositories;
- produces or loads sanitized signed evidence fixtures;
- imports a disabled profile;
- converts it into an AssistX admission candidate;
- issues a test-only expiring projection and route authorization;
- proves router acceptance of valid contracts and rejection of stale, tampered, mismatched, revoked, and capacity-duplicating variants;
- records the exact four-repository commit set in the result.

## P1 — operational hardening gaps

### P1.1 Production telemetry and alerting

Export and alert on:

- controller lease/fencing health;
- reservation and claim expiry/conflict rates;
- runtime projection freshness and rejection reasons;
- per-priority queue depth, timeout, cancellation, and saturation;
- path selection and failover state;
- migration, recovery, degraded journal, and replay state;
- improvement attempt acceptance/failure/promotion state;
- evidence age, signer policy, admission lease expiry, and rollback-health age.

### P1.2 Retention and cleanup policy

Define operator-visible retention for:

- signed evidence bundles and manifests;
- controller checkpoints and migration checkpoints;
- canary logs and raw benchmark outputs;
- patch artifacts and abandoned worktrees;
- old runtime projections and admission leases;
- degraded journal archives and restore evidence.

Deletion must preserve required audit fingerprints and must not remove the only rollback evidence for an admitted runtime.

### P1.3 Calibration and confidence aging

Allocation uses quality, throughput, reliability, latency, cost, and opportunity cost, but production recommendations need:

- larger samples per model/loadout/task family;
- confidence intervals or evidence strength;
- age/freshness penalties;
- explicit extrapolation warnings;
- drift detection after runtime, model, quantization, template, or hardware changes.

### P1.4 Cohort rollout and fleet error budgets

After the first physical node succeeds, add:

- one-node-at-a-time cohort promotion;
- automatic pause when fleet error budgets are consumed;
- cohort rollback to last-known-good profiles;
- limits on simultaneous migrations, recoveries, or runtime changes;
- operator-visible rollout state and stop reason.

### P1.5 Signing-key lifecycle and evidence revocation

External allowed-signers policy exists, but the live system still needs a complete operational lifecycle:

- key issuance and role separation;
- rotation without invalidating required historical evidence;
- explicit compromise/revocation records;
- signer identity and namespace constraints;
- admission lease reevaluation when a signer or evidence root is revoked;
- backup and disaster recovery for trust policy.

### P1.6 Control-room completeness and documentation consistency

`/control-room` is the canonical operator interface and legacy UI paths redirect to it. Current documentation and operational material should consistently use the canonical path and distinguish current pages from compatibility redirects.

The remaining UI work includes incident timelines, topology views, accessibility, clearer degraded-state explanations, admission lease status, evidence age, projection generation, and explicit rollback readiness.

### P1.7 Recovery and improvement deployment across capable nodes

Recovery and bounded self-improvement are implemented but opt-in. Each capable node still needs:

- repository root and isolated worktree configuration;
- node-specific evidence-signing key;
- central verification policy;
- crash cleanup proof;
- key rotation exercise;
- one signed recovery canary;
- one bounded improvement canary;
- stale-claim and stale-promotion rejection proof.

## P2 — strategic capability gaps

### P2.1 KV-cache remains metadata/record-first

The control plane and LMS toolkit can identify compatible prompt-prefix/cache candidates, but portable save/restore is not yet production-proven.

Required sequence:

1. deploy runtime-specific llama.cpp slot and SGLang HiCache adapters;
2. prove exact model/quant/tokenizer/template/runtime compatibility;
3. measure save, transfer, restore, hit correctness, TTFT, and throughput;
4. test expiry, eviction, corruption, and restart behavior;
5. keep LM Studio/vLLM affinity-only until an approved portable restore boundary exists;
6. enable distributed replication only after local behavior is proven.

### P2.2 Improvement loop does not yet close through a trusted publish workflow

AssistX can produce and verify bounded patches and can safely apply an exact accepted fingerprint, but intentionally stops before commit, push, PR creation, merge, or deployment.

A future trusted publishing service may consume an operator-approved accepted attempt and open a draft PR while preserving these invariants:

- the producing agent cannot approve or publish itself;
- the publisher revalidates base SHA, patch digest, paths, tests, and repository cleanliness;
- branch, commit, and PR identity are recorded in Neo4j;
- merge remains an independent human or policy-gated action;
- production deployment and outcome measurement are separate stages;
- measured results feed back into the skill profile only after verification.

### P2.3 LMS product and release engineering

Older LMS roadmap issues include several completed items, such as the `src/` package layout, pytest suite, and packaged assets. Remaining lower-priority product work should be re-triaged rather than treated as release blockers:

- tagged wheel/sdist release workflow;
- exact LM Studio CLI output fixtures and compatibility policy;
- effective configuration inspection and precedence;
- structured JSON logging;
- optional benchmark history database/dashboard/exporter.

## Documentation and backlog hygiene

1. Keep the four HLD/LLD pairs authoritative and update them in the same PR as contract changes.
2. Add a cross-repository compatibility matrix with schema/API versions and supported commit ranges.
3. Mark dated fleet observations as historical evidence, not current admission truth.
4. Update or close stale LMS issues #4 and #5 after comparing their acceptance criteria with current implementation.
5. Maintain one system-level issue in `auto-assist` and link repository-specific execution issues from it.

## Recommended execution order

### Stage 1 — first physical proof

Complete `lms#7` on one noncritical node and import one real non-admitted profile.

### Stage 2 — admission contract

Implement AssistX admission candidate/lease records, freshness/revocation checks, and a profile-to-candidate importer.

### Stage 3 — router authority integration

Replace production file-backed runtime admission with the current AssistX projection and require reservation/signed route authorization where applicable.

### Stage 4 — cross-repository CI

Gate the complete evidence-to-router contract against pinned commits from all four repositories.

### Stage 5 — one-node live canary

Grant a short-lived admission lease, verify path/capacity/priority behavior, collect telemetry, and automatically revoke/rollback at expiry.

### Stage 6 — degraded recovery rehearsal

Execute the Beelink/standby restore, activation, journal, replay, promotion, relinquishment, and rollback sequence.

### Stage 7 — cohort expansion

Add cohort rollout, error budgets, continuous freshness/rollback monitoring, and production alerting before broader fleet admission.

## Definition of system-ready

The system is ready for a controlled production cohort when:

- one physical node has complete signed observation, qualification, canary, rollback, and negative-drill evidence;
- a real profile has been imported and remains disabled by default;
- AssistX has issued an exact, expiring, revocable admission lease after current live revalidation;
- auto-router consumes only the current approved projection and enforces durable route authorization where required;
- LAN/Tailscale paths share one measured slot pool and fail over without hidden fallback;
- cross-repository contract CI is green for the exact deployed commit set;
- production telemetry and rollback alerts are active;
- degraded recovery has been physically rehearsed;
- cohort rollout can stop and rollback automatically without granting agents self-approval or release authority.
