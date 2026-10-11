# AssistX graph-entry release gate delta — October 11, 2026

**Decision:** GO for continued disposable research; **NO-GO** for production graph cutover and automatic failover.  
**Current draft:** [#300](https://github.com/scottjoyner/auto-assist/pull/300) stacked on #296→#295→#294→#275→#273→#264→#260.  
**Primary tracker:** [#148](https://github.com/scottjoyner/auto-assist/issues/148).  
**Previous formal release report:** [October 11 release readiness](https://github.com/scottjoyner/auto-assist/blob/research/assistx-task-policy-projection-parity-20261011/docs/TRACE_GRAPH_ADMISSION_RELEASE_READINESS_20261011.md).

## What has changed materially

| Gate | Previously | New October 11 evidence | Disposition |
|---|---|---|---|
| B02 Graph consumer inventory | Unbounded/unmapped callers | 187 Python source files scanned, **229 static candidate sites in 51 modules** (33 constructors, 132 low-level, 18 read, 46 write candidates); full file/line/owner manifest and fail-closed scan tests committed | **Partial:** inventory GO; field/side-effect parity and consumer migration NOT accepted |
| B03 Real broker-on-Raft gate | Physical Raft and physical isolated broker separately proven | **Actually composed physical PASS, 78.16s**: real three-voter Raft CAS from private policy service precedes broker's real Neo4j Task read. Worker has no direct Bolt/graph HTTP, read-only graph role denies CREATE; replay and changed-plan ID conflict denied. Writer shutdown denies next broker graph request and retains committed Raft reservations | **Narrow research subgate PASS**; full production graph-action B03 still unaccepted |
| B05 Writer service principal | Research shared UID / identity assumptions | Docker user namespaces caused real container root to appear as **host UID 65534**; assuming host UID 0 resulted in fail-closed rejection. Now a disposable peer-discovery canary pins the observed numeric UID without disabling SO_PEERCRED. | **NO-GO:** UID 65534 is not a unique cryptographic service identity; separate OS principals + key custody still needed |
| B06 Physical closure | Independent detached witness proven on a separate real Neo4j subprobe | New broker deliberately **retains RESERVED state after successful reads and writer death**; it does not synthesize a witness or free slots based on HTTP success | **NO-GO:** independently verified, durable closure is not wired to this exact Task transaction |
| B01 Running Neo4j isolation | Direct API/worker path observed | No live cutover, network or credential change in #300 | **FAIL still present** until migration + staging deny proof |

**Source and actual physical evidence:** [joined B02/B03 report](https://github.com/scottjoyner/auto-assist/blob/research/assistx-graph-consumer-release-gates-20261011/docs/TRACE_B02_B03_UNBLOCKING_20261011.md), [all 229 candidate source locations](https://github.com/scottjoyner/auto-assist/blob/research/assistx-graph-consumer-release-gates-20261011/research/evidence/assistx-graph-consumer-inventory-20261011.json), [disposable full-chain observation](https://github.com/scottjoyner/auto-assist/blob/research/assistx-graph-consumer-release-gates-20261011/research/evidence/assistx-b02-b03-joined-raft-broker-20261011.json).

## Exact physical claim and limitations

Manual opt-ins (all required): `ASSISTX_DISPOSABLE_GRAPH_BROKER=1 ASSISTX_THREE_HOST_RAFT_RESEARCH=1 ASSISTX_BROKER_RAFT_RESEARCH=1`. The exact joined run **PASS** at source SHA `cc385e16803094f75ae11083454ce284814bfa8b`, 78.16 seconds, exit code 0. The final published research code at `d8f84e95c3ee93ee7e3f8b6584a2c605e18a551a` incorporates evidence, reporting and a metadata-label fix, with **246/246** combined offline tests passing on the final SHA. The physical fixture remains a separate explicitly gated command, not a hosted CI job.

The service writer was a trusted host test process/thread with a private Unix socket mapped read-only into the **disposable broker only**. It held temporary Raft credentials, not live production credentials. The worker and broker had only a synthetic bearer and a server-controlled fixture identity, not production verified users or mTLS. The new graph read was real and restricted, but the full independent witness proof from #260 ran in a **different** subprobe. No automatic capacity release or successful failover was claimed. All disposable resources were removed.

## Still blocking the production release

**P0 B01:** Migrate all consumers before removing live Bolt access; current API and worker can reach the graph directly.  
**P0 B02:** Map 229 candidate sites to real runtime ownership/type and preserve all write, claim/recovery, review, heartbeat and completion semantics. A static scan is not full compatibility signoff.  
**P0 B03/B06:** Require real Raft grant **and** detached generation-aware graph transaction closure witness in *one exclusive broker pipeline*; verify at-most-one physical read during quorum/graph restarts.  
**P0 B04/B05:** Prove authenticated principal provenance from Tailnet/API through broker, separate service identities (not mapped UID 65534), independent Raft writer and signer custody, no raw KV.  
**P0 B07/B09:** Real etcd NOSPACE/restore/snapshot safety, bounded durable replay history (128 tombstones is research-only), backpressure, site failures and recovery.  
**P0 B08/B10 (trace index track):** Original physical Redis/Neo4j in-flight read cap, representative production-equivalent query load and authenticated browser release gates in [#148](https://github.com/scottjoyner/auto-assist/issues/148) / [#123](https://github.com/scottjoyner/auto-assist/issues/123). These are *not* closed by a Task broker.  
**P0 B11:** Feature flags default OFF, explicit operator approval, canary metric/alerts, rollback exercise, exact artifact/commit and access-policy provenance.

## Shortest next plan

**First:** use the generated consumer manifest to select a single read-only *staging* caller that does not affect claim/recovery state, with source field-by-field parity and a read-only rollback switch. **Second:** attach the real detached Neo4j transaction witness to the exact broker-owned Task transaction, proving an independent signed physical closure and server/generation binding; never release based on a simple HTTP result. **Third:** add actual separate service principals and confidential credentials, not just a shared host-side Docker mapped UID. **Fourth:** stage a network-deny test and end-to-end regression only for the migrated caller; do not disconnect all production Neo4j clients.

**Decision as of this report:** B03 narrow read-chain integration newly demonstrated; R1 disabled-only staging is closer but still requires approval and service identity; R3 exclusive production graph broker and R4 automatic failover **NO-GO**. No production configuration changed.
