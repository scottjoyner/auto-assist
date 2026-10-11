# AssistX Trace / Graph Entry — Release Readiness Review
**Assessment date:** 2026-10-11, 12:41 ET (updated after live read-only verification)  
**Decision:** **NO-GO — production graph admission, physical in-flight fencing, and automatic failover**  
**Reviewed authority:** [Issue #148](https://github.com/scottjoyner/auto-assist/issues/148), original [performance/browser issue #123](https://github.com/scottjoyner/auto-assist/issues/123), research PRs [#260](https://github.com/scottjoyner/auto-assist/pull/260), [#264](https://github.com/scottjoyner/auto-assist/pull/264), [#273](https://github.com/scottjoyner/auto-assist/pull/273), [#275](https://github.com/scottjoyner/auto-assist/pull/275), [#294](https://github.com/scottjoyner/auto-assist/pull/294), [#295](https://github.com/scottjoyner/auto-assist/pull/295), [#296](https://github.com/scottjoyner/auto-assist/pull/296). All listed PRs remained **OPEN / DRAFT / UNMERGED** when checked.

## Executive assessment

**Architectural feasibility is high; production enforcement is not accepted.** Disposable physical experiments have demonstrated 3-voter etcd Raft capacity CAS on x1-370/xwing/destroyer, quorum-loss denial, a real Neo4j transaction observed by a detached closure witness, exact-key etcd RBAC, policy-writer Unix IPC, committed-success/lost-ACK replay rejection, synthetic verified HTTP idempotency, and a network-isolated read-only Neo4j Task broker.

**The current live fleet still bypasses the proposed boundary:** on x1-370, a fresh *read-only, TCP-connect-only* probe on October 11 returned **`NETWORK_BYPASS_PRESENT_NO_GO` (intentional exit 2)**. Both running `assistx-api` and `assistx-worker` share network access with `neo4j` and can connect directly to Bolt (7687) from their containers. Neo4j's Bolt port remains host-published via a wildcard mapping (7688→7687). Both actors have Neo4j URI/user/password/database environment **variable names** configured; secret values were never printed or used. TCP handshake and presence of credential *names* **do not prove ability to authenticate, run Cypher, or write graph data**. Nonetheless, a hard claim that all worker-to-graph communication is nonbypassable is false at the network layer.

A separate conditional trusted-login-header code path accepts a configured Tailscale identity header if the login matches the allowlist. Synthetic untrusted-peer helper tests demonstrate the helper itself does not authenticate proxy origin. A live nonallowlisted header returned 403; this was **not** an impersonation of a real allowlisted user, and does not prove a live auth compromise. Deployment proxy provenance remains to be verified.

There are **two release tracks**:
1. **Physical graph-entry/failover:** multi-host Raft + isolated broker + independently durable transaction closure and credential/identity enforcement.
2. **Authenticated trace-history UI/index:** `GET /api/traces` performance, index admission and actual physical in-flight cap, plus real authenticated browser acceptance. This originated in issues #148/#123 and is **not cleared** by an isolated Task read broker.

## Evidence completed — what can accurately be called PASS

| Capability | Accepted evidence and scope | Remaining limitation |
|---|---|---|
| 3-host Raft majority/minority | Physical disposable voters on x1-370, xwing, destroyer; quorum loss blocks new admission, surviving reservation preserved, leader transition exercised (#256/#260) | No independently powered fault domains, production term/restore ownership or production cutover |
| Real graph transaction witness | Actual disposable Neo4j Bolt transaction survives Raft loss; independent signer observes closure and gates exact release (#260) | Server does not require a cryptographically authorized Raft permit for every direct Bolt client; no cluster-wide generation-proof closure |
| etcd writer RBAC and IPC | Separate policy-writer *process* holds scoped credential; gateway-style client holds Unix socket only; actual Raft CAS physically demonstrated (#264) | Research processes share OS UID; harness administers credentials; not independently protected production key custody |
| Lost ACK and replay | Real quorum commit with reply discarded at test adapter; exact-ID retry and changed payload refused; tombstones and read-only reconciliation (#273) | Not a literal packet partition, and live client API does not use these paths |
| Verified HTTP identity prototype | Synthetic Basic-auth HTTP → Unix writer → real 3-host Raft CAS, stable principal/key/digest, duplicate 409 and status GET (#275) | Research endpoint unmounted; production Tailscale proxy identity and streaming retries unverified |
| Live network topology gate | Running API and worker directly reach Neo4j via TCP; wildcard host Bolt mapping (#294), reconfirmed Oct 11 | This is an explicit **FAIL** for production isolation; no Cypher/auth bypass claimed |
| Isolated broker / Neo4j RBAC | Physical synthetic worker isolated from graph by Docker bridge, fixed read plan through broker, actual Neo4j read-only Task role blocks CREATE (#295) | Only disposable environment, no admission term or witness embedded in broker |
| Policy read projections | Separate `get_task_read` and `get_task_claim_guard` plans, exact-source recovery/drain policy parity for representative fixtures, payload-only runbook case (#296) | Not full `Neo4jClient.get_task` parity for claim/review/heartbeat/complete |
| Origin-spoof negative | Research worker client rejects broker hostname suffix, URL userinfo, path/query/fragment and alternative ports (#296) | Static fixture origin pin is NOT production mTLS or service identity |
| Focused CI | Exact PR #296 head `d93967fd8ae30f193cb1f0802092920384cb2789`: **227/227** local focused tests. At inspection, **all four hosted workflows GREEN** for #296 and #295 | All checks are research/CI evidence; no deployed production acceptance, operator approval or site recovery |
 
**Important:** physical tests listed in different rows may have been performed in separate *subprobes*. In particular the synthetic HTTP path does not itself run a real Neo4j Bolt read through the new isolated broker. Do not claim an integrated, production-equivalent transaction chain.

## October 11 B02 caller-census reconciliation

[Dedicated reproducible caller-census report](./TRACE_NEO4J_CONSUMER_CENSUS_20261011.md) now identifies **72 Neo4jClient methods, 149 potential method call sites, 194 direct session sites outside the client, and 57 files with graph call indications**. These numbers are *conservative static source evidence*, not unique active queries or an executed authorization audit. B02 is now **mapped/partially analyzed, still P0 NOT ACCEPTED**.

The nine source-level `get_task` call sites demonstrate why the two narrow broker plans are insufficient for general migration: review approval needs `title` fallback and task/dispatch writes; replan reads then directly writes `SET`; completion invokes improvement evaluation/benchmark logic; heartbeat and review rejection/clarification require separate behavior/authorization gates. A graph read alone cannot remove the live worker network bypass.

Latest reviewed source and consumer-guard head `c73532b9f9c5a292d53674467f486e5c48561e2d`: **233/233 focused local tests pass**. This is research-only CI, not staging or release approval.

## Open blockers — release-stopping

| ID | Priority | Unresolved gate | Exact acceptance required to change status | Responsible discipline |
|---|---|---|---|---|
| B01 | **P0** | Live worker/API direct Bolt/HTTP paths and graph port publication | After verified consumer migration, worker/API Docker namespaces, other fleet nodes and alternate host/Tailnet paths cannot reach Neo4j directly; only broker has least-privilege graph access; every auth bypass negative preserved. **Current state: FAIL.** | Fleet networking / graph |
| B02 | **P0** | Full graph caller and Task policy compatibility | Inventory all Neo4j reads/writes in API, worker, review, heartbeat, claim, completion, dispatch and health; per-consumer static projection/command registry; differential fixture output and authorization parity, including no recovery-node identity downgrade and no queue-class misclassification. **Partial (two helper functions only).** | AssistX API / policy |
| B03 | **P0** | Physical Raft grant inside actual exclusive broker | An authenticated exact-operation admission reaches quorum CAS *before* the actual broker-owned Neo4j Bolt transaction, without an alternate direct path; stale epoch/term, lost ACK and replay denials observed on the same integration chain. **Not wired.** | Broker / coordination |
| B04 | **P0** | Trusted production identity across HTTP/proxy/IPC | Validate Tailscale Serve trusted-header stripping and direct localhost/bridge access; bind verified user/request ID to immutable plan digest, peer credentials and broker identity. Test synthetic spoofed header from every path, concurrent two-user separation, reconnect and stream cancellation. **Unproven.** | Auth / API |
| B05 | **P0** | Separate trust principals and secret custody | Broker, gateway, policy writer and witness execute as genuinely distinct OS/service identities; credentials scoped, secret mount/process/procfs and socket permission negatives demonstrated; raw etcd writer key not obtainable by gateway or worker. **Same UID research fixture.** | Platform / security |
| B06 | **P0** | Independent durable Neo4j physical closure proof | Verifier-only signed custody survives crash before/after fsync and Raft CAS; graph restart, tx ID reuse, failover, lost member, disputed/absent observations, forged signer or signer outage never free capacity prematurely. **Research witness only.** | Database / audit |
| B07 | **P0** | Consensus restore/space/outage safety and rejoin | Actual etcd NOSPACE/compaction, lost ACK, snapshot restore, copied-state rollback, revoked quorum member and orphan-slot recovery drill; monotonic epoch and server identity cannot regress or authorize conflicting owner. **Not accepted.** | Coordination / SRE |
| B08 | **P0** | Trace index physical in-flight cap under actual API | Replace/contain Redis TTL-overlap counterexample from #154/#160/#166; authenticate before counting; demonstrate exact physical cap 1/3/5/10 under multiworker cancellations, Redis outage and 4s per-query deadline, without auto-release after TTL or missing HTTP reply. **Not cleared by Task broker.** | Trace API / data |
| B09 | **P0** | Production capacity/liveness and replay ledger durability | Replace tiny research completed-operation history bound of 128 without replay windows; demonstrate archive/compaction durability, backpressure and authenticated tenant fairness, alerting, p95/p99 and recovery. **Fail-closed research prototype only.** | Platform / SRE |
| B10 | **P0 for trace-index UI release** | Staging query performance and real authenticated browser acceptance ([#123](https://github.com/scottjoyner/auto-assist/issues/123)) | Budgeted production-equivalent 85k-group `EXPLAIN`/DB-hit/p95/p99 and count/page under load, actual auth/session/429/unknown handling, keyboard/mobile/screen reader review and rollback. **Synthetic-only coverage is not release signoff.** | Trace API / UI |
| B11 | **P0 for any production enablement** | Rollout ownership, telemetry and rollback | Named operator approves gated staged canary, exact commit/container hash and config, SLO/alerts for held capacity and graph bypass, operator can revert identities/network and routes, no automatic failover until physical negative matrix passes. **Not done.** | Release operator / SRE |

## Other open gates (P1 or separately scoped)

- **Operator load and fairness:** 1/3/5/10 authenticated callers, mixed tenants/NAT, end-to-end traces, DB hits, p95/p99, cancellation/broker restart; do not extrapolate from fast unit tests.
- **Task field/data-classification breadth:** consumer-specific policies for review, completion, administrative operations; payload sensitivity, retention and no accidentally broadened read privilege.
- **Off-host evidence custody:** verify externally durable append-only receipts, source provenance and separate full tool-call history audit ([issue #117](https://github.com/scottjoyner/auto-assist/issues/117)). UI trace groups are not complete historical custody.
- **Real independent failure domains:** correlated power/Tailscale outage, recovery on another host/site, time/skew and cold reboot sequence, capacity preserved during recovery.
- **Documentation/release packaging:** consolidate stacked draft dependency DAG and authoritative release manifest to prevent integration from silently promoting research fixtures.

## Conditional release ladder

| Track | Present disposition | What would unlock it |
|---|---|---|
| R0: Research-only PRs / disposable test acceptance | **GO for continued research review**, not merge/promotion | CI GREEN, security findings and limitations documented; tests have no production side effects. |
| R1: Disabled-by-default **staging-only, read-only broker canary** | **CONDITIONAL**, not approved now | B02 basic consumer parity, B04/B05 service identity, B01 staging isolation, explicit resource/rollback plan. Test feature switch is OFF by default and no production graph writes. |
| R2: Production `GET /api/traces` index / investigative UI | **NO-GO** | B04, B08, B09, B10, B11 plus issue #123; cannot rely on Redis TTL as physical concurrency fence. |
| R3: Production exclusive broker with graph actions | **NO-GO** | All B01–B07, B09 and B11; real graph entry must be nonbypassable and witness-verifiable, beyond Task read fixture. |
| R4: Automatic failover / autonomous takeover | **HARD NO-GO** | R3 plus independently governed fencing term, physical closure, site-fault and restore/rejoin tests, explicit operator approval and rollback. |

**There is no meaningful production GO based solely on test count or green CI.**

## Next execution order (minimum dependency chain)

1. **Caller inventory and trust graph** (B02): map `Neo4jClient`, direct `_session`, the Task claim/recovery/review decision fields, graph writes and direct connection paths. Mark every consumer unmigrated until parity tested.
2. **Exclusive broker + Raft/witness joined in one physical chain** (B03/B06), preserving replay semantics and read-only Neo4j role in a disposable isolated fixture; run an explicit negative worker direct Bolt attempt.
3. **Principals and ingress hardening** (B04/B05): prove no real gateway client can forge another user, writer or witness; do not use a forwarded login header without verified trusted ingress.
4. **Budgeted staging migration and reachability gate** (B01): preserve existing API behavior; remove *only the staged* direct Neo4j paths, validate deny from all relevant namespaces, then test rollback. Do not sever current production paths before parity.
5. **Physical fault and replay matrix** (B06/B07/B09): graph restart/term change, signer crash, raft leader/minority, etcd NOSPACE/snapshot, lost acknowledgement, two-user concurrency and historical ledger expiry.
6. **Trace index separately** (B08/B10): multiworker physical cap, query-performance budget and actual authenticated browser/a11y; stage only if issue #123 and #148 acceptance pass.
7. **Production go/no-go review** (B11): sign exact released SHA, signed configuration and evidence references; preserve separate gates for R2 trace index, R3 graph broker and R4 failover.

## Proposed final release record template

```yaml
release: assistx-graph-entry
assessment_date: 2026-10-11
decision: NO_GO
candidate_sha: null                  # no authorized production release candidate
test_head: d93967fd8ae30f193cb1f0802092920384cb2789
test_pr: 296
focused_local_tests: 227
hosted_checks_at_test_head: 4/4_GREEN
research_physical_broker: PASS_DISPOSABLE_ONLY
live_network_gate: FAIL_NETWORK_BYPASS_PRESENT_NO_GO
real_api_auth_and_streams: UNVERIFIED
raft_permit_integrated_in_broker: false
independent_durable_witness: false
production_Bolt_network_isolated: false
rollback_exercised: false
operator_signoff: null
production_admission_enabled: false
automatic_failover_enabled: false
blocking_issues: [148, 123]
blocking_prs: [294, 295, 296]
next_allowed_action: research_and_explicitly_approved_staging_tests_only
```

**Safety of the report itself:** all live checks were read-only TCP/Docker inspect and source inspection. No production credential value, Neo4j query, live API mutation, network change, container restart, trace payload or user identity impersonation was attempted.
