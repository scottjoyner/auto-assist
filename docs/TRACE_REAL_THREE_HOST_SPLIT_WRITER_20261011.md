# AssistX B03/B05 — Joined three-physical-host split writer + isolated Neo4j broker

**Date:** October 11, 2026  
**Status:** DRAFT research · **physical PASS, production NO-GO**  
**Parent:** [PR #306](https://github.com/scottjoyner/auto-assist/pull/306); [issue #148](https://github.com/scottjoyner/auto-assist/issues/148)

## The genuinely new evidence

Previous three-host work demonstrated real Raft, scoped etcd RBAC and an independently witnessed Bolt transaction in **separate research subprobes**. Previous isolated graph broker work used three Raft voters **on one host**. This slice combines the **same isolated worker request** with:

```text
x1-370: Worker --[worker internal]--> Graph broker
                                          |
                        [graph-only] Neo4j 5.26 Enterprise
                                          |
                     [separate internal writer RPC bridge]
                                          |
                          Separate policy-writer container
                                          |
                     [temporary outbound Docker bridge]
                                          |
             ephemeral mTLS + exact-key RBAC etcd JSON gateway
                  /                      |                  \
           x1-370 voter               xwing voter       destroyer voter
```

The worker, broker, writer and Neo4j are all **disposable research containers**. Only the writer receives the research TLS client certificate/private key and the **dedicated exact-key etcd RBAC bearer**. The worker never receives that authority; the broker receives only a fixture writer-RPC bearer and graph-reader credential. The broker's direct numeric-IP connection to the actual Tailscale etcd client port fails. The writer cannot connect directly to the disposable Neo4j IP/ports. Worker direct graph, writer and etcd access also fails. No published host ports are created for the broker, worker, writer or graph.

### First actual physical joined experiment

**PASS**. Three actual machines (`x1-370`, `xwing`, `destroyer`) participated in the Raft quorum; a scoped RBAC writer performed authoritative CAS before the **same** broker ran the static Neo4j Task read. The real Neo4j reader account could MATCH selected Task fields but could not CREATE. The three reservations stayed pending with zero closures after HTTP success. A fourth logical request was denied; duplicate request and changed-plan/idempotency conflicts were rejected. **Joined section: 56.95 seconds.**

A separate privileged research administrative client verified that the **new** `joined-broker-policy` credential could **not** read the *other* RBAC service's key. This demonstrates an actual exact-key authority scope boundary. It does not prevent a stolen *writer* credential from directly mutating its own allowed key.

### Second physical experiment: majority lost while broker remains online

**PASS**. A fresh set of disposable voters was created on all three physical hosts. After the same isolated broker admitted exactly three requests, the harness stopped **only the uniquely named research voter containers on xwing and destroyer**, leaving the x1-370 voter as a minority. A new authenticated worker request reached the broker and was rejected with **HTTP 503 `ADMISSION_UNCERTAIN`**, without any fallback Neo4j read. The harness restarted those exact voters in a `finally` guard and independently re-read the restored Raft document: **3 pending, 0 completed**.

**Full three-host run:** exit 0, approximately 86.78 seconds. **Joined broker portion:** 63.55 seconds. All temporary containers, certificates, Raft data directories and networks were scoped by random research IDs and removed. Live Neo4j, AssistX, Tailscale ACLs, NAS and running services were untouched.

[Machine-readable physical evidence](../research/evidence/assistx-real-three-host-split-writer-20261011.json).

## Reproduction, only in an approved temporary lab

```bash
ASSISTX_THREE_HOST_RAFT_RESEARCH=1 \
ASSISTX_RAFT_RBAC_RESEARCH=1 \
ASSISTX_THREE_HOST_SPLIT_WRITER_RESEARCH=1 \
ASSISTX_DISPOSABLE_GRAPH_BROKER=1 \
python3 tests/probe_trace_etcd_three_hosts.py
```

Requires image, SSH and Docker readiness on **all three** physical hosts. The existing three-host harness provisions an ephemeral CA and mTLS peer/client credentials, binds etcd to **temporary Tailscale-specific ports**, creates an independent exact-key `joined-broker-writer` role and `joined-broker-policy` user **before** enabling etcd auth, and destroys research infrastructure in `finally`. The new callback creates only a disposable read-only Neo4j/broker/writer/worker fixture.

Implementation:

- `tests/probe_trace_etcd_rbac_physical.py`: optional **separate** exact-key RBAC authority+principal for the physically joined broker; negative denial for an unrelated key.
- `tests/trace_disposable_separate_policy_writer.py`: uses `EtcdBearerTLS` for real mTLS + etcd RBAC, retaining a separate explicit disposable local HTTP mode.
- `tests/probe_trace_isolated_task_broker.py`: creates distinct IPC + egress bridges; injects private TLS client files and RBAC bearer **only into writer**, never broker or worker; uses numeric-IP network negatives; stops/restarts only exact research voters for failure injection.
- `tests/trace_disposable_joined_raft_worker.py`: uses the **actual randomized three-host Raft port**, not the old local `:2379`, when proving worker bypass denial.
- `tests/test_trace_three_host_split_writer_guards.py`, `tests/test_trace_three_host_joined_minority_guards.py`: synthetic boundary and cleanup assertions. GitHub CI runs offline checks and syntax compilation **only**, not physical Docker/SSH deployment.

## Release gate scoring

| Gate | Result | Reason it remains open |
|---|---|---|
| B01 live direct Neo4j bypass | **FAIL / NO-GO** | Current production API and worker still have direct Bolt path. |
| B02 all graph-client consumers | **PARTIAL / NO-GO** | #301 source census identified 149 potential graph method calls and 194 direct sessions; full semantic parity not accepted. |
| B03 joined three-host Raft→broker entry | **PHYSICAL PARTIAL PASS** | Real three-host admission, scoped auth and minority denial now proven **in the joined read-only fixture**; no independent closure/signer or server stale-effect fence. |
| B04 authenticated production user identity | **NO-GO** | The tested ingress principal is fixture-fixed; trusted Tailnet/HTTP auth and 2-user receipts remain unverified. |
| B05 writer separation and credentials | **PHYSICAL PARTIAL PASS** | Writer has scoped mTLS+RBAC into Raft, but broker→writer uses cleartext HTTP fixture bearer and OS identities/secret custody are not independently protected. |
| B06 physical witnessed closure | **NO-GO** | The prior detached witness ran in **another subprobe**; no Neo4j txID/instance generation/signed independently observed closure is wired into this joined broker. |
| B07 restore, quota, correlated faults | **NO-GO** | Same Tailnet, no independent site/power proof; snapshot/NOSPACE/credential revocation drills still missing. |
| B08–B11 production acceptance | **NO-GO** | Query concurrency cap, trace UI performance/browser, durability/load and signed release/rollback separately outstanding. |

**Important residual:** the *writer's own* exact-key RBAC token can issue raw KV updates within its permitted key; this fixture does not cryptographically enforce application policy at the etcd layer. A compromised broker can issue arbitrary well-formed stable IDs to the writer for the two fixed plans. This is not a server-side Neo4j fencing implementation, nor proof that one old query can never survive a term transition.

### Next narrow engineering slice

Use a separate **detached Neo4j transaction witness with signed, durable, generation-pinned custody** inside the *same* isolated broker run; bind original member/generation/txid to each reservation, deny forged or uncertain closure, and demonstrate no capacity release until real transaction absence is independently observed. Separately move broker↔writer to identity-verifying RPC (mTLS or proven separate-UID Unix socket), eliminate the synthetic ingress identity and prohibit raw direct user-to-graph paths *only after complete consumer parity and operator-reviewed rollback*.

**Bottom line:** The actual three-host security boundary now works for this tested, disposable, isolated Task read, including a real two-voter minority. Production admission and automatic failover remain disabled; issue #148 stays open.
