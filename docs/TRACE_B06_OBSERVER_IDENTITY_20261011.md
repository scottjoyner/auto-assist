# AssistX B06 — Exact observer identity and absence-only gate (research)

**October 11, 2026 · Draft stacked on PR #310 · Release status: NO-GO**

## Outcome and separation of evidence

This slice adds an **observation-only** validation contract for the pre-existing detached Neo4j Bolt witness in the earlier three-host quorum experiment. It does **not** integrate that witness into the independently built isolated Task broker in [PR #308](https://github.com/scottjoyner/auto-assist/pull/308) or the graph-restart negatives in [PR #310](https://github.com/scottjoyner/auto-assist/pull/310).

The new `tests/trace_b06_observation_gate_research.py` enforces: an exact seven-key `assistx_*` metadata tuple, a unique server-observed original Neo4j transaction ID, an observer principal different from the broker, a pinned member and boot/generation, complete observations, and strictly increasing observation sequences. For a previously observed active transaction, a pair of absent observations is rejected if the original transaction remains visible, the same metadata appears on another transaction, the graph member/generation changes, or the observer identity changes.

**Crucial authorization boundary:** both `ObservedActive` and `ObservedAbsenceOnly` have `signer_authorized=False` and `release_authorized=False`. They contain no signature, signing key, closure receipt or writer method. These predicates are **necessary checks, not sufficient grounds to free a reservation**. The member/boot identity supplied by the existing fixture is *not independently server-attested*; a forged observation caller could spoof it. Do not promote any successful observation into production closure.

### Physical compatibility check — PASS

Command:
```bash
ASSISTX_THREE_HOST_RAFT_RESEARCH=1 \
ASSISTX_QUORUM_NEO4J_RESEARCH=1 \
ASSISTX_B06_OBSERVER_GATE_RESEARCH=1 \
python3 tests/probe_trace_etcd_three_hosts.py
```

**PASS, exit 0**, against fresh disposable Raft voters on **x1-370, xwing, destroyer**, and a disposable Neo4j 5.26 Enterprise Bolt instance. The previously developed detached witness process observed a real active transaction with exact original metadata while two voters were stopped; the minority denied a competing execution, reserved capacity remained occupied, active closure was rejected, and after the original physical transaction disappeared the existing research signer completed its receipt/term-transition test. The stricter new observer predicate checked exact original transaction presence and absence-only consistency; those checks themselves never sign.

[Machine-readable evidence](../research/evidence/assistx-b06-observer-identity-20261011.json).

**This physical Bolt witness subprobe has `NEO4J_AUTH=none`, does not enforce etcd exact-key RBAC, and explicitly reproduces an authenticated raw-KV policy bypass in a separate research key.** It is materially less restricted than the isolated three-host broker path from #308 and is not a substitute for production custody.

### Offline regression contract

`tests/test_trace_b06_observation_gate_research.py` checks exact metadata, missing/ambiguous transactions, changed plan/attempt/term/reservation, different observer or member, reboot, incomplete `SHOW TRANSACTIONS` rows, alternative transaction with reused metadata, transaction-ID reuse, stale/replayed observation sequences and the explicit absence of signer/release authority. `tests/test_trace_quorum_neo4j_physical_guards.py` now requires the physical detached witness code path to invoke the strict predicates before its *existing*, separately gated signing step.

The full local research suite and hosted CI must be checked at the exact branch head. The physical experiment is manual opt-in; GitHub Actions must **never** SSH to lab machines, bring up a database or try to sign/release production reservations.

## Unresolved original B06 release blocker

| Gate | Status |
|---|---|
| Strict active/absent observer predicate | **PASS in existing physical Bolt witness subprobe** |
| Three-host Raft mTLS + exact-key RBAC isolated broker admission | **PASS in separate PR #308 physical subprobe** |
| Joined broker graph restart retains all reservations | **PASS in PR #310 physical subprobe** |
| **One same-request broker → Bolt metadata → independent observer chain** | **NOT IMPLEMENTED** |
| Observer independently authenticates graph member/boot generation | **NOT IMPLEMENTED** |
| Independent witness signer/key custody persists across crash | **NOT IMPLEMENTED** |
| Signed exact physical closure → scoped writer CAS → durable tombstone | **NOT IMPLEMENTED in joined broker** |
| Server-level stale-term effect fencing | **NOT IMPLEMENTED** |
| Production worker/API direct Neo4j bypass removed | **FAIL / B01 live** |

### Next minimum physical experiment

Replace the joined broker's one-shot `/db/neo4j/tx/commit` path **for a single opt-in research operation** with an explicit Bolt transaction, tagged with immutable operation/attempt/reservation metadata. A **separate credentialed observer** must see the exact server transaction while it is active, and capture independently authenticated member+boot identity before any Raft binding. The experiment should initially produce an **observation-only receipt** while keeping the slot occupied; a restart, ambiguity, observer outage or unknown generation must deny signing. Only a later durability/signature/key-custody acceptance should enable release through the exact-key writer.

**NO-GO:** Production graph admission and automatic failover remain disabled. B01 live bypass, B02 graph consumer parity, B04/05 production identity, B06 durable closure, B07 disaster recovery, and B08–B11 load/trace/release gates remain unresolved. Keep #148 open and every PR draft.
