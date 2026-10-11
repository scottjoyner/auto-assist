# AssistX B03/B05 — Physically separated Raft policy writer and graph broker

**2026-10-11 · Disposable research only · Production NO-GO**

## Result

A fresh physical x1-370 **Docker-only** experiment PASSED in **62.51 seconds**; the final strengthened authentication/network run also **PASSED in 63.63 seconds**. It builds on [draft #302](https://github.com/scottjoyner/auto-assist/pull/302), which had allowed the isolated graph broker itself to perform raw etcd CAS. This slice **removes the broker's direct network access to etcd** and moves Raft CAS into a separate, fixed-plan HTTP policy-writer container.

Four strictly internal, temporary Docker networks were created without published ports, production mounts, Tailscale configuration changes, or references to live AssistX/Neo4j services:

```text
Worker            --[worker-only]--> Graph broker --[graph-only]--> Neo4j
                                      |
                              [writer-IPC-only]
                                      |
                              Separate policy writer
                                      |
                                [Raft-only]
                                      |
                             3 real etcd voters
                            (ALL ON x1-370)
```

Only the writer has direct network access to the disposable Raft authority. Only the broker has direct network access to the disposable Neo4j reader account. The worker can access neither. The broker holds a fixture writer-RPC bearer token, **not the raw KV endpoint or CAS access**. The writer knows no graph connection or reader password. Both services are still simple Python containers without production mTLS, scoped etcd RBAC or independent protected OS identities.

## Physical acceptance details

The same authenticated research worker request drove **fixed Task plan → stable request derivation → broker-to-writer admission RPC → real 3-voter etcd quorum CAS → approved Neo4j read**, using only static source-reviewed Cypher. The writer enforces the allowlisted plan IDs `get_task_read`/`get_task_claim_guard`, exact `auth:<sha256>` stable operation key and exact 64-digit digest. It independently verifies the pinned genesis, owner and term before reserving capacity.

The physical run reported:

- **PASS**: real Raft CAS was required before the physical Neo4j query.
- **PASS**: worker direct Bolt, graph HTTP, etcd and writer-IP connections denied.
- **PASS**: broker direct etcd-IP connection denied; writer retains own raw etcd access.
- **PASS**: policy writer direct Neo4j Bolt/HTTP connections denied; broker-to-writer request without a bearer returned HTTP 401.
- **PASS**: duplicate logical request and changed-plan-with-same-key denied.
- **PASS**: three pending reservations retained, zero closures; fourth request denied.
- **PASS**: after stopping two temporary etcd voters, a fresh worker request received **HTTP 503 ADMISSION_UNCERTAIN**, with no broker-side raw graph fallback.
- **PASS**: restricted Neo4j reader could read approved Task data but `CREATE` was denied.
- **PASS**: no fixture containers/networks remained; no running production service, database or storage changed.

[Structured physical result](../research/evidence/assistx-separated-policy-writer-20261011.json).

## Source and reproducibility

- `tests/trace_disposable_separate_policy_writer.py`: standalone fixed-plan Raft admission service, secure-by-denial response normalization; uses disposable Raft HTTP transport.
- `tests/trace_disposable_task_broker.py`: added `RemotePolicyAdmission`, exact writer host/port pin, bearer-only RPC, opaque reservation validation, no fallback to direct Raft on writer failure.
- `tests/probe_trace_isolated_task_broker.py`: extra opt-in `ASSISTX_DISPOSABLE_WRITER_SPLIT=1`, four private bridges and separate process/container wiring. Original no-Raft and joined self-writer modes remain disabled by default.
- `tests/test_trace_disposable_separate_policy_writer.py`: focused offline tests reject unsupported plans, untrusted writer URLs, invalid receipts, stale owner, transport failure and missing-writer-token negative.
- `tests/trace_disposable_writer_unauth.py`: manual-only broker-to-writer RPC without bearer; expects HTTP 401. No secret material in command line.
- Research GitHub workflow: offline tests and syntax compilation only. Hosted CI never launches the physical experiment.

Exact physical command on the authorized x1-370 host:

```bash
ASSISTX_DISPOSABLE_GRAPH_BROKER=1 \
ASSISTX_DISPOSABLE_RAFT_IN_BROKER=1 \
ASSISTX_DISPOSABLE_WRITER_SPLIT=1 \
python3 tests/probe_trace_isolated_task_broker.py
```

All disposable Docker resources use random names and have scoped cleanup.

## Three-host next-gate readiness (read-only, no deployment)

A separate read-only SSH/Docker inspection on October 11 contacted **x1-370, xwing, and destroyer**. Batch SSH, Docker and a local Tailscale IPv4 check succeeded for all three. Every host had `quay.io/coreos/etcd:v3.6.14` cached with the **same repository digest**, `linux/amd64` architecture, image creation metadata and **matching hashes of the 17 rootfs layers**. Local Docker image IDs were different across x1-370 versus xwing/destroyer; this does not alone establish different binary contents given the agreeing digest and layer fingerprint. No binary attestation, container start, port binding, mTLS check or authenticated writer admission occurred during the preflight. [Structured three-host preflight](../research/evidence/assistx-three-host-writer-preflight-20261011.json).

**Infrastructure availability alone is not proof that the broker has been joined to the multi-host Raft quorum.** The actual split-writer physical experiment above remains **one-host research only**.

## Still NO-GO — critical authority gaps

1. **No true independent Raft failure domains.** Three real etcd voters are **all on x1-370**. Rejoin the actual x1-370/xwing/destroyer research Raft deployment via mutually authenticated, RBAC-scoped writer transport.
2. **No production-grade writer authentication.** RPC is an ephemeral bearer token over **cleartext internal Docker HTTP**, not mTLS/peer-authenticated IPC, and a compromised broker can submit arbitrary stable operation IDs for the two permitted plans. Broker process identity and independently verified ingress user identity remain unproven.
3. **No separate protected OS principals or key custody.** Containers are separate network namespaces but not separately administered Linux service accounts with tested socket permission/credential extraction denial. Etcd's own RBAC is disabled in this single-host rig.
4. **No independent physical graph witness.** The broker does not bind a live query's original Neo4j member/generation/txid or obtain a durable signed, independently observed absence receipt. A successful Neo4j/HTTP response never frees slots.
5. **No server-side fencing of stale already-running Neo4j effects.** A privileged broker might bypass its own policy checks; Neo4j's read role alone does not enforce Raft application term.
6. **Production B01/B02 remain unresolved.** Running AssistX API and worker still directly reach Neo4j Bolt. The current source census detects 149 potential method-call sites plus 194 direct sessions beyond the central client, with no approved complete feature-by-feature migration/rollback.
7. **Operational gates not accepted.** Crash durability, signer restart, etcd restore/NOSPACE, authenticated multiuser load, trace-index bounds, and signed release/rollback are outstanding.

**Disposition:** Network-path privilege separation is proven in this single-host disposable fixture. B05 is **partially advanced**, B03 is **partially advanced**, B01 remains **LIVE FAIL**. Keep issue #148 open, every related PR draft, production graph admission and automatic failover disabled.
