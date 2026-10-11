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

## October 11 B03 joined-request feasibility checkpoint

[New physical joined-broker experiment](./TRACE_JOINED_RAFT_ISOLATED_TASK_BROKER_20261011.md) now demonstrates **real quorum CAS before an actual Neo4j read inside one isolated broker request**: the worker has no direct Bolt/HTTP or Raft network; replay and changed-plan key rejected; 3 pending slots remain and a fourth admission is denied. **Physical PASS, 62.77s**, on x1-370 with *three real Raft voters on that one host*. See [machine-readable evidence](../research/evidence/assistx-disposable-joined-raft-broker-20261011.json).

A further **fresh physical majority-loss run PASSED (62.56s)**: after committing three broker-owned reservations, the experiment stopped two disposable Raft voter containers, and a new worker request received **503 ADMISSION_UNCERTAIN**, with no unguarded read fallback. This is a one-host Raft minority fault, not multi-site failover proof.

**B03 remains P0 / partially proven, not accepted** because this fixture uses single-host etcd with internal unauthenticated HTTP, no scoped mTLS or writer RBAC, no independent txID/generation closure witness, no separate secure secrets, and no true 3-host joined transaction. In particular, a successful HTTP read must never be treated as permission to release the three outstanding reservations. The real production worker/API still bypass this broker (B01 FAIL), and the caller census remains not migrated (B02 OPEN). Automatic failover remains HARD NO-GO.

## October 11 B05 separate policy writer checkpoint

[Separated broker/writer physical handoff](./TRACE_SEPARATE_POLICY_WRITER_20261011.md) advances **B03/B05 network-path separation**. A **real disposable** four-private-bridge test on x1-370 runs worker→broker→fixed-plan policy writer→three local etcd voters before a restricted Neo4j Task read. Broker has no direct etcd IP route; worker has no direct writer or graph route; writer has no direct Neo4j route. A broker-to-writer RPC without bearer was denied **401**. Duplicate/changed-plan replay, exact cap=3 and majority-loss fail-closed negatives passed. Final physical run **63.63s**, scoped cleanup verified.

**Release remains NO-GO:** etcd HTTP/IPC are internal cleartext bearer-based research mechanisms, all voters share x1-370, writer/broker are not independently protected OS identities, and no independent generation-pinned Neo4j transaction witness or signed closure has been integrated. The worker/API's **live** direct Bolt access remains B01 FAIL; B02 full callsite migration is outstanding. This is **B05 partial, not B05 acceptance**. [Machine-readable evidence](../research/evidence/assistx-separated-policy-writer-20261011.json).

## October 11: physically joined THREE-host Raft → separate writer → isolated Neo4j broker

[Dedicated physical handoff](./TRACE_REAL_THREE_HOST_SPLIT_WRITER_20261011.md) documents the first **joined** broker/writer/Neo4j fixture actually backed by three Raft voters on **x1-370, xwing and destroyer**, using ephemeral mTLS and **exact-key etcd RBAC**. A separate writer, not the broker, holds Raft credentials. Worker→writer/Neo4j/etcd, broker→etcd, and writer→Neo4j numeric-IP bypasses are denied. A writer RPC without bearer is rejected, Neo4j's reader cannot CREATE, repeat/conflicting requests are rejected and **three pending/zero closed** are preserved.

**Final physical fault PASS:** stop the disposable voters on xwing and destroyer while the joined broker stays running → fresh authenticated worker request receives **503 ADMISSION_UNCERTAIN**; restart both named voters in `finally` → **3 pending, 0 completed** observed after recovery. Full run exit 0 (~86.78s), joined section ~63.55s. [Machine-readable evidence](../research/evidence/assistx-real-three-host-split-writer-20261011.json).

**Score:** B03 **partial physical PASS** for real three-host quorum-before-read and minority denial; B05 **partial physical PASS** for writer-only mTLS/RBAC and network segregation. B01 **LIVE FAIL**, B02 **OPEN**, B04 production ingress identity **OPEN**, B06 generation-bound detached witness in this *same* execution **OPEN**, B07 restore/NOSPACE **OPEN**, B08–B11 **OPEN**. Broker→writer still cleartext fixture bearer, OS identities not independently protected and the writer key can perform raw exact-key etcd operations. Production graph admission and automatic failover remain **NO-GO**.

## October 11 B06 verified-negative graph restart checkpoint

[Joined physical graph restart and closure-denial report](./TRACE_JOINED_CLOSURE_NEGATIVES_20261011.md) records a PASS on **disposable Neo4j 5.26 + real Raft across x1-370/xwing/destroyer**. The isolated worker→broker→separate mTLS/RBAC writer had **3 pending/0 completed** after Task reads; physically stopping and restarting only the disposable Neo4j container retained **3 pending/0 completed** throughout, as proven by a fresh quorum read from the writer. A forged `close_with_witness` claim on each original **RESERVED, unbound** attempt returned **NO_VERIFIED_PHYSICAL_BINDING**. Majority-loss denial remained in force. The complete physical script exited 0, joined section 88.34s, with no production changes.

**B06 remains HARD NO-GO for release.** This demonstrates **absence of premature automatic release**, not independently signed transaction closure. Current HTTP `/tx/commit` broker records no server-observed transaction ID/boot generation, has no independent signer or durable custody, and cannot safely release a reservation. [Exact run evidence](../research/evidence/assistx-b06-graph-restart-closure-negative-20261011.json). B01 live bypass, B02 full consumer migration, and all other production gates remain open.

## October 11: exact active Bolt transaction observer (separate subprobe, release NO-GO)

[Research B06 observer identity report](./TRACE_B06_OBSERVER_IDENTITY_20261011.md) adds strict fail-closed observation-only predicates (exact gateway metadata, unique original transaction ID, different observer principal, member+boot pinned, complete reads, monotonic and nonambiguous absence sequence). The existing *separate* three-host physical Bolt witness subprobe passed with these predicates enabled, including active transaction during majority loss, no premature release, and the old detached signer completing its existing tested closure path. [Machine-readable evidence](../research/evidence/assistx-b06-observer-identity-20261011.json).

**B06 NOT CLOSED:** `release_authorized` is explicitly false for both observation results; graph member and boot are still fixture-supplied rather than independently attested; the older Bolt witness uses `NEO4J_AUTH=none` and no etcd RBAC, with a raw-KV bypass reproduced on a separate disposable key. The strictly isolated three-host broker from #308/#310 still uses one-shot HTTP and does NOT wire this witness into its *same* request/closure chain. B01 live direct Bolt bypass remains FAIL; release remains **NO-GO**.

## October 11: first SAME-REQUEST explicit Bolt transaction inside the 3-host isolated broker

[New B06 physical handoff](./TRACE_B06_JOINED_HELD_BOLT_OBSERVER_20261011.md) records **PASS** for the genuine **worker→isolated broker→separate writer→3 physical host etcd mTLS/exact-key RBAC CAS→explicit broker-owned Neo4j Bolt transaction→separate observer-only Neo4j account**. The broker holds the third accepted reservation's original Bolt transaction open until a second graph-only research service uniquely observes its exact `SHOW TRANSACTIONS` metadata; after the broker commits, that service observes absence under the strict #312 contract. The separate observer has no graph CREATE, Raft writer key, broker auth or signing/release capability. A fourth operation is denied at three pending slots; loss of the real two-voter majority denies subsequent new operations. Full run **PASS exit 0**, three-host harness ~105.37s, joined broker segment ~77.57s; tests/docs only, scoped resources removed.

**This is a significant B06 partial physical PASS, but B06 is still P0 NO-GO:** the member/boot label is fixture-authored rather than independently attested, the original txid/boot is not bound to Raft in the same broker, the observer is unsigned and its absence-only result has `release_authorized=False`, no independent durable signer custody or exact signed release exists, and server-side stale-term effect fencing remains missing. Production B01 direct Bolt bypass and B02 full caller migration remain unresolved. [Exact evidence](../research/evidence/assistx-b06-joined-held-bolt-observer-20261011.json).

## October 11 B06: exact observer transaction bound to original Raft reservation as STARTED

[Physical B06 original-transaction binding report](./TRACE_B06_OBSERVED_RAFT_BIND_20261011.md) now verifies the SAME isolated worker→broker→separate policy writer→real three-physical-host Raft mTLS/RBAC→explicit Bolt transaction. A separate Neo4j observer sees the original live transaction. **While Bolt remains active**, the privileged disposable coordinator passes its observed row to the scoped writer, which rejects a forged txid, compares the exact reservation/metadata/owner/term against an authoritative Raft snapshot, and atomically changes **RESERVED→STARTED with the observed original member/fixture generation/txid**. Replayed binding is refused. After Bolt completion, ordered absence observations are recorded; no slot is freed (**3 pending, 0 completed**). Real 2-voter minority failure still denies new admission. **Physical PASS**, 3 hosts x1-370/xwing/destroyer, joined 79.85s, full run 106.43s; no production change. [Evidence](../research/evidence/assistx-b06-joined-observed-raft-bind-20261011.json).

**B06 still RELEASE NO-GO:** member/boot identity comes from the disposable fixture, not an independently attested graph identity; the privileged coordinator can fabricate coherent observation JSON, so the binding is NOT independently verified signed custody. No detached durable signature, closure CAS, or capacity release is wired. This is a significant **research STARTED binding PASS**, not authenticated physical closure. B01 live direct Bolt bypass, B02 caller parity and the other release gates remain unresolved.

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
