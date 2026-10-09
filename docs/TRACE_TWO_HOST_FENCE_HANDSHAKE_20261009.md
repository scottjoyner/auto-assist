# #148 two-host independent fencing witness — October 9, 2026

**Status: DRAFT RESEARCH, NO PHYSICAL DISPATCH AUTHORITY, NO FAILOVER.** Parent [#227](https://github.com/scottjoyner/auto-assist/pull/227) established the two-origin central SQLite admission, physical copied-journal split-brain counterexample and stale-checkpoint rollback. This slice adds a separate, nonexpiring capacity witness on xwing. No production API, Neo4j, Redis, Tailscale, NAS, model routing, provider calls, credentials or services changed.

## 2-of-2 design and explicit limits

**x1-370 local owner:** same `SingleAuthorityResearch` from #227, holding a capacity-one reservation, pinned UUIDv4 epoch, graph identity, monotonically increasing per-file sequence, and no release.

**xwing independent witness:** new `IndependentResearchWitness` under `src/assistx/trace_two_host_fence_research.py`, with its own exact `/tmp/assistx-twohost-fence-test-*/witness-test.sqlite` 0600 file, different host, a bootstrapped term, graph and epoch, `BEGIN IMMEDIATE`, synchronous FULL, inode/mode checks, `mode=rw`, nonexpiring occupied token, and uniqueness of sequence/nonce/token/query. No automatic bootstrap, promotion, expiry, release or master election.

**x1-370 wrapper:** `tests/probe_trace_two_host_fence_handshake.py` first commits to its local admission ledger, then uses **preexisting authenticated SSH** to request xwing's exact matching record (epoch, graph, term, sequence, token, receiver nonce, query identity). It refuses a missing/failed/stale/mismatched witness reply. **Every response contains `physical_dispatch_permitted=false`, even a witnessed research preparation.** If the witness is unreachable after x1 has reserved, x1 **retains** its reservation rather than rearming or doing local fallback. Remote command is bound to xwing, no new listeners/ports. The wrapper's term/epoch are supplied by a research CLI and **are not independent operator-provisioned authority credentials**. Existing host SSH permissions are not production RBAC.

A passed research witness means TWO durable local records agreed about ONE proposed work identity; it **does not mean** an actual remote query can execute or that physical work has completed. The witness does not verify a Neo4j transaction or independently provision its own term from a quorum. All code is outside AssistX runtime.

## Executed real two-host evidence (x1-370 and xwing)

Both hosts fetched the same research head into detached worktrees and independently passed **11/11 local synthetic fence tests**.

### A. Successful independent witnessed grant
- x1-370 bootstrapped a fresh disposable owner; xwing separately bootstrapped its own witness for epoch `529b2872-6ec4-4b73-bf08-7e80774db262`, synthetic graph `a` × 64 and witness term **7**.
- x1 reserved sequence **1** and requested acknowledgment via x1→xwing authenticated SSH; xwing durably recorded matching term 7 and sequence 1.
- Wrapper returned `two-of-two-research-witnessed` with **`physical_dispatch_permitted=false`**.
- Independent observations: x1 owner `active=1,sequence=1`; xwing witness `occupied=1,highest_sequence=1,term=7`.
- A syntactically valid term-7 successor was denied by xwing as **`physical-capacity-held`**. A stale term-6 request returned **witness-unavailable**, never a grant.

### B. Lost witness after owner reservation
- Separate disposable owner epoch `854e3a1d-9146-4c4f-8df1-47dbda777292`; the SSH route to xwing was intentionally failed using `ProxyCommand=/bin/false` (without actual network/firewall change).
- Owner committed one reservation before the witness call. Wrapper returned **`held-witness-unavailable, physical_dispatch_permitted=false`**, and independent owner read showed **`active=1,sequence=1`**.
- The held reservation was not rearmed. This models delivery refusal, **not** actual bidirectional network partition.

### C. Restore x1 snapshot while xwing remains authoritative
- x1 bootstrapped a new disposable epoch `45cb6b10-5fe3-4fcd-b3e2-dc85444c5197`, took a BEFORE-admission file snapshot; xwing separately bootstrapped a term-**12** witness.
- A request physically originating on **xwing**, sent over SSH to the x1 wrapper and back to xwing witness, received a matching term-12, sequence-1 **witnessed-but-not-dispatchable** result.
- xwing independently reported `occupied=1,highest_sequence=1`. The initial empty x1 journal was then restored in place, retaining its file inode.
- x1's **rolled-back owner** produced another local sequence-1 reservation, but xwing's non-rolled-back witness refused the replacement. Wrapper returned **`held-witness-not-accepted, physical_dispatch_permitted=false`**.
- This demonstrates a **single-host rollback fence while the OTHER host remains uncompromised and retains its state**. It does not protect against simultaneous rollback of both owner and witness or against cloned witness files.

## Offline synthetic acceptance and counterexamples

`tests/test_trace_two_host_fence_research.py` tests:
- two independent local journals and exact first grant
- 1/3/5/10 candidate waves after a held token => zero accepted
- wrong term, graph or epoch deny; missing witness refuses to recreate
- x1 owner rollback cannot bypass a surviving witness
- a failed remote acknowledgment leaves a reserved owner and zero permission
- spoofed/mismatched witness response never authorizes dispatch
- **EXPECTED FAIL-SAFETY COUNTEREXAMPLE:** copy an empty witness journal to a second independent path; both copies can accept different grant identities. This is *not* consensus.

Prior #227 tests demonstrate that two independently copied owner ledgers also both issue grants and that an xwing independently retained sequence watermark detects x1 rollback only when a current watermark is used.

## Open acceptance gates — DO NOT CLOSE #148

1. **Independent monotonic fencing authority:** a witness term is pinned *locally* and static; there is no quorum, election, epoch promotion, durable revocation, distributed consensus, hardware-monotonic store or Byzantine defense. Both owner AND witness could be rolled back or cloned together. No automatic failover is safe.
2. **Verified actor and receipt custody:** bare SSH on these lab accounts does not grant production RBAC or independent operator-approved key provenance. The preissued receiver nonce research from #224 is not atomically bound to this external witness or to physical server transaction identity.
3. **Physical query admission:** these are synthetic grant proposals; no actual Neo4j queries, physical query termination/release, or delegated remote worker commands occur. Require authenticated multiworker two-host physical graph staging, independently witnessed Neo4j transaction ownership, lost-network/management-plane scenarios, p95/p99, failure injection and rollback.
4. **Other gates:** trusted ingress #149, PII/role scope, full source review, independent staging negative tests and operator-controlled launch.

**Disposition:** fail-closed 2-of-2 synthetic handshake demonstrated on real nodes, including denial after a one-host rollback. It remains draft, no release authority, and is not a production distributed-failover design.
