# AssistX #148 — policy-writer IPC separation, next research slice

**2026-10-10 · DRAFT / research only · stacked after #260 · production/failover NO-GO**

## Why this exists

Draft #260 physically joined a real three-host etcd Raft reservation to a real disposable Neo4j transaction and independent closure witness. It also proved that mTLS alone permits a direct raw KV rewrite of term/owner, and separately proved real etcd exact-key RBAC with a restricted reader and scoped writer user on the three-host research cluster. **Even with RBAC enabled, possession of the policy-writer credential permits raw CAS modification of its assigned authority key.** Therefore the gateway must not possess that credential: only a policy-enforcing writer process can safely hold it.

This slice implements the narrow policy-writer interface for offline research with **actual Linux Unix-domain socket communication**, plus a separate-process test. No production daemon or API route is changed, and the physical three-host experiment from #260 is not rewritten.

## Code

`tests/trace_etcd_policy_writer_research.py` implements three roles:

- `PolicyEngine` holds a pinned, single-namespace `EtcdQuorumFence` using raw write capability. That capability may be constructed with scoped bearer RBAC credentials inside a dedicated service process; it is **never returned to callers**. The engine pins genesis, owner, term, Raft cluster and a static allowlist of reviewed plan IDs.
- `UnixPolicyWriterServer` serves small JSON requests on a newly created, mode `0600` Unix socket within a `0700` directory. Linux `SO_PEERCRED` checks the peer UID. No TCP listener. Only `admit` with server-pinned owner/term, `status`, and `close` with an independently Ed25519-signed *exact physical-binding* receipt are available. Arbitrary raw `Txn`, free-form Cypher, client-selected terms, owner overrides, TTL release and unsigned takeover are **not** supported.
- `UnixPolicyClient` and `UnixPolicyGrantAdapter` are a drop-in authority for `ProtectedGraphEntry`. They contain a Unix socket path and pinned genesis, **not** etcd TLS client keys, RBAC bearer tokens, operator signatures or direct raw KV methods. IPC failure returns an explicit uncertain refusal, never a free reservation. The server redacts low-level quorum exceptions.

## Executed evidence on x1-370

The new `tests/test_trace_etcd_policy_writer_research.py` has **13 passing tests** using a deterministic fake KV *behind a real Unix socket* (not a fake socket):

- Actual admission over socket, exact outstanding Raft reservation and cap one.
- Unrecognized query plan, malformed operation ID, arbitrary Cypher, caller-supplied term, raw-Txn and unsigned takeover **all denied before admission**.
- Same-host peer UID mismatch denied by real Linux `SO_PEERCRED`.
- Wrong signer cannot close occupied capacity; correctly signed receipt can close exact bound transaction and replay is denied.
- Real `ProtectedGraphEntry` uses the Unix adapter to execute one reviewed plan and still holds the reservation after graph driver success.
- Oversized message, non-private socket directory and absent writer all fail closed.
- Actual second OS process hosts the Unix socket server and accepts one gateway request. It does **not** share its raw KV Python object with its client.

**Parent #260 local suite: 107/107 focused tests passed. New isolated policy-writer tests: 13/13 PASS.** Run the combined 120-test suite and hosted exact-head CI before claiming the new PR fully green.

## Threat model boundaries

1. **Not deployed to live three-host etcd:** the 13 tests use the offline `FakeEtcd` store and real Linux Unix sockets. They validate service protocol/credential containment shape, not quorum linearizability. The physically tested RBAC from #260 is separate.
2. **Not a separate Unix security principal yet:** a same-UID process can impersonate a legitimate client if it reaches the private socket path. Production needs distinct OS users/groups or a container isolation boundary, rotated IPC credentials, and stronger process-level secret custody.
3. **Bearer credentials not yet exclusively issued to this service on the live cluster.** Provision scoped etcd writer keys in a protected store, ensure actual gateways have only reader (or no etcd) permission, verify direct raw KV attempts from gateway OS UID/namespace are denied, and test policy-writer compromise.
4. **Witness not independently durable:** current closure signature verification is correct for the provided input, but no external append-only signed receipts, generation-aware cluster attestation, or post-restart recovery have been integrated here.
5. **Neo4j still doesn't check the term itself.** A privileged process with direct Bolt connectivity could bypass this service, and existing queries can outlive owner/consensus loss.
6. **No real API workload, operator approval or rollback.** Full 1/3/5/10 authenticated clients, p95/p99, proxy/NAT fairness, tracing lineage, and production approvals remain out of scope.

## Next physical gate

Run the policy-writer as a **distinct OS principal** on a disposable actual three-host RBAC quorum, with sole possession of the exact-key write bearer token. Run a gateway process in a separate UID/namespace with only the Unix RPC client and reviewed plan allowlist, and a third independent closure-signer principal. Prove that a compromised gateway can neither direct-write etcd nor connect to Bolt bypassing the gateway; blackout/kill/partition must preserve physical in-flight occupancy, and an independently durable signed receipt alone must authorize release. Keep all production systems untouched.

**Decision: research-only; #148 OPEN; production admission/failover disabled.**
