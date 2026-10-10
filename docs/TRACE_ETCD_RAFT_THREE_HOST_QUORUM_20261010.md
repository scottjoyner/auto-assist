# AssistX #148 — real three-host Raft quorum fencing, physical research acceptance

**2026-10-10 | DRAFT research, no production authorization | Branch `research/assistx-raft-quorum-three-host-20261010` stacked on PR #253**

## Design objective and scope

The previous PR #253 reproduced two independently bootstrapped PostgreSQL 17 primaries (same research epoch, cap 1 each) independently admitting, and cloning a local SQLite authority plus checkpoint admitting twice. The missing primitive was *actual* consensus over a single linearizable owner/term/reservation key—not a lease, locally incremented counter or duplicated single-primary SQL.

The new research implementation uses a real etcd Raft consensus group with **three separate physical members on x1-370, xwing and destroyer**. All three run the same pinned `quay.io/coreos/etcd:v3.6.14` digest `sha256:dfd3941bf6ced5fdb700f9b2d98b22b7bca7ceee13aec16224f93ff30d9a59c4`. The three machines are distinct but share a tailnet and could share correlated network/power failure domains. This **does not** prove three independent sites, a production topology or geo-DR.

Experimental containers use a unique generated cluster token and new research-only key prefix, peer and client TLS with an ephemeral CA, mutual client certificate authentication, Tailscale IPv4-specific HTTPS bind addresses, non-root UID 1000, read-only rootfs, dropped capabilities and strict temporary file modes. The only writable host binds are run-scoped temporary etcd data directories under `/home/scott/git/.assistx-raft-<id>/...`. **Host Docker network mode means a Tailscale-reachable listener during each probe; never configure this unreviewed research fixture as a production network service.** There is no public port publication, no production data, no systemd/Tailscale/firewall edits, and no persistent voters after teardown.

## Source-of-truth artifacts

- `tests/trace_etcd_quorum_fence_research.py` — application-level, research-only fencing logic above etcd v3 JSON gateway, using explicit HTTPS mTLS, a linearizable `Range` (never `serializable=true`), atomic `Txn` compare by etcd `mod_revision`/create `version=0`, and a single authoritative JSON state under an isolated prefix. An opaque per-attempt reservation persists through leader failure and quorum loss (NO TTL). `bind` records exact server identity/generation/transaction ID. `close_with_witness` requires Ed25519 receipt and exact binding; `takeover` requires no outstanding attempt, distinct next owner, and an operator-signed term transition. **This code is not called by production graph services.**
- `tests/probe_trace_etcd_three_hosts.py` — explicit `ASSISTX_THREE_HOST_RAFT_RESEARCH=1` manual physical orchestrator, unique certs, three different host voter processes, mTLS negative, real leader stop/reelection, loss of quorum and direct raw etcd write rejection, restart/rejoin, cleanup.
- `tests/test_trace_etcd_quorum_fence_research.py` — eight no-Docker offline CAS/signature/rollback/copy counterexample tests. Fake store is NOT a Raft test.
- `tests/trace_etcd_graph_admission_research.py` — NEW unwired `QuorumPlanGrantAdapter`: bridges quorum CAS reservation to the existing allowlisted `ProtectedGraphEntry` interface using trusted, pinned genesis/cluster ID/owner/term. A real reservation is committed before graph intent, never released by driver return, timeout, or audit failure. **This is an offline research interface; no actual Neo4j query used it in the three-host physical probe.**
- `tests/test_trace_etcd_graph_admission_research.py` — seven no-Docker gateway integration negatives: unregistered Cypher, stale owner after signed clean takeover, quorum loss, audit failure, graph timeout, pinned cluster required, and capacity persistence.
- `tests/test_trace_etcd_three_hosts_guards.py` — five no-Docker static safeguards for opt-in, node isolation, mTLS, quorum negative and teardown.
- `research/evidence/assistx-raft-three-host-20261010.json` — machine-readable observation record, source revision, pinned image digest, chronological event order and explicit limitations.
- `.github/workflows/trace-graph-entry-research.yml` — hosted focused tests and syntax/opt-in checks; **never runs the distributed physical probe in hosted CI**.

## Executed physical experiment on October 10

**Final hardened run: PASS, ~21.34 seconds on x1-370 controlling disposable voters on x1-370/xwing/destroyer.** It used the probe source from commit `8a2229a64f7e886b1afdaed77ae7371b6c3d6119` (subsequent changes add tests/evidence/workflow only).

| Physical negative / property | Observed |
|---|---|
| Three voters on distinct machines, single cluster identity | PASS |
| No client certificate attempted against TLS-protected endpoint | DENIED |
| One `RESERVED` persistent intent created via xwing client endpoint | PASS |
| Additional admission via destroyer etcd endpoint, global cap 1 | DENIED |
| Actual current Raft leader identified via maintenance status, then stopped | PASS |
| Remaining two voters elected new leader (Raft term advanced) | PASS |
| Existing pending reservation on two-voter majority | PRESERVED |
| Takeover while unresolved physical reservation | DENIED |
| Separate *empty* test namespace, signed takeover term 1→2 | PASS |
| Stale old owner after takeover | DENIED |
| Stopped leader restarted and observed term 2 | PASS |
| Two voters stopped, lone survivor asked for **linearizable read** | DENIED |
| Lone voter issued **direct etcd Txn write** (not application read-before-write) | DENIED |
| Both voters restarted, term 2 present and minority write key absent | PASS |
| Original unresolved intent after election, partition and all restarts | STILL RESERVED |
| Temporary vote containers and research dirs | All gone in cross-host post-run check |

The only positive takeover occurs on a deliberately separate, empty authority namespace. The original pending graph intent **never** receives a fabricated physical closure, never ages out and never frees its capacity. This is a safety/liveness distinction: without independent physical closure reconciliation, progress must stop.

### Failure-to-fix history

1. Initial Raft start failed on all nodes: capability-dropped root container could not traverse/read owner-restricted mounted certificate files (`open /certs/node.pem: permission denied`). Logs captured before exact scoped teardown.
2. A proposed scoped root ACL change failed with `Invalid argument` on x1-370's research filesystem. No production permissions were altered.
3. Final implementation ran as UID/GID `1000:1000`, matching the `scott` owner on all three hosts; used mode 0700/0600 temporary owner-only cert/data binds, dropped all capabilities and retained read-only container root filesystems. Real quorum acceptance subsequently passed.
4. Post-run cleanup inspection found three **empty**, old `.assistx-raft-*` staging directories on destroyer (from earlier attempts); removed only those exact empty directories with `rmdir`. Final cross-host check found zero temporary containers/staging directories. All three voter images resolved to the same pinned digest.

### Tests and audit scope

- Parent research branch PR #253: 70 focused tests PASS, dedicated research workflow and all four parent PR workflows GREEN.
- New Raft CAS offline tests: 8 PASS.
- Five extra explicit-opt-in/static research guard tests added, followed by seven offline quorum-to-gateway adapter tests. Combined exact-head suite: **90/90 focused tests** to verify at latest commit after adding the adapter (the earlier 83/83 suite passed locally).
- Real three-host Raft probe was run manually on fleet nodes; no hosted CI job can use the distributed SSH/Docker credentials.
- Research runtime event history is a sanitized ordered observation log in `research/evidence/`. Temporary TLS *private keys*, bootstrap files, test cluster storage and on-host transient runtime logs are deliberately not retained in a Git repository. The observation log has no independently anchored signatures and is not production audit custody.

## Safety boundaries — what is NOT proven

1. **Not real graph-effect fencing.** The new `QuorumPlanGrantAdapter` offers an offline interface to the existing query gateway, but no disposable or production Neo4j process has yet consumed the quorum token in a real Bolt transaction. A stale in-flight Bolt transaction could keep running after term loss; this three-host experiment contains **no Neo4j transaction**.
2. **Not external closure custody.** The witness interface in this authority accepts a correctly formatted Ed25519 signature but a graph server/release verifier was not involved in the three-host run. Signed externally immutable event/closure evidence remains a separate requirement.
3. **Not a hardened authorization service.** mTLS protects endpoints, but etcd's keyspace RBAC for restricting PUT/TXN to an independently privileged fencing service has not been enabled or tested. A credential authorized to raw-write the prefix could bypass the Python policy and overwrite terms, owner, or pending attempts. The operator key is held by the disposable coordinator, not an independent human/HSM.
4. **Not independent sites or geo resilience.** Machines share the same operational tailnet and possibly the same power/network failure domain. Three separate physical machines and true Raft majority were demonstrated, but not 3-site independence.
5. **Not arbitrary HA takeover.** The positive signed takeover succeeded only with zero outstanding work. Any old reservation in `RESERVED`, `STARTED`, or `UNCERTAIN` blocks the next owner. A true orphaned-before-Bolt proof must be independently attested before release.
6. **Not production QoS.** No actual 1/3/5/10 concurrent authenticated mobile/API clients, trusted proxy/NAT identity, p95/p99 load, service rollback, operational SLOs, production credentials or durable retention approvals were touched.

## Next smallest safety gate

1. Introduce a server-side guarded graph-entry admission adapter on the disposable #252 Bolt fixture consuming a **quorum-issued opaque reservation** and checking exact genesis, fencing term and owner. Never give workers direct graph network/credentials. Test a stale old owner after a signed empty transition, then an old **active** Neo4j query while quorum temporarily disappears; never release/claim safety due to a timeout or Raft election.
2. Move policy writes behind an independently authenticated fencing server and enforce etcd scoped RBAC; test direct raw unauthorized transaction rejection with a client permitted to read but not mutate authority. Separate operator signer, verifier signer and gateway grant identities.
3. Connect a generation-aware independent Neo4j observer, immutable externally anchored witness receipts, and fail-closed key custody; enforce release via validated signed receipts, not verifier password possession.
4. Repeat partition/leader loss with independently governed operation IDs and preserved authoritative traces; combine the #252 physical crash/blackhole and #253 copied-state negatives, then run 1/3/5/10 authenticated client acceptance, rollback and operator approval.

**Disposition:** Research GO for three-host single-quorum fencing primitive only. **#148 remains OPEN; production admission and distributed failover NO-GO.** No automatic takeover or graph write authority was deployed.

Relevant official references: https://etcd.io/docs/v3.6/learning/api/ (linearizable Range, atomic transactions, Raft term and revision distinction), https://etcd.io/docs/v3.6/learning/why/ (why a lease alone does not establish mutual exclusion).
