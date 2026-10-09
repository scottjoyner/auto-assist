# AssistX #148 — Two physical host origins, single-owner admission and copied-ledger failure

**Date:** 2026-10-09. **Research-only**. Parent: draft [#224](https://github.com/scottjoyner/auto-assist/pull/224), which preregisters receiver nonce/token/query evidence in a local journal. This experiment addresses the *admission authority* fault separately. It uses **x1-370** and **xwing** with existing passwordless SSH, not production Neo4j, Redis, TLS ingress, or AssistX routing.

## Scope and safety

The new `src/assistx/trace_two_host_authority_research.py` is independent of runtime imports. An exact disposable SQLite owner must live under `/tmp/assistx-twohost-authority-test-*/authority-test.sqlite`, with 0600 mode, pinned UUIDv4 epoch and graph identity, capacity 1, no expiry, no release method, strict `mode=rw` opens, `BEGIN IMMEDIATE`, and `synchronous=FULL`. It atomically issues a token, receiver nonce, and monotonic sequence when reserving. No worker may derive an authorization from loss of SSH, a stale local journal, or a supplied receiver receipt.

The CLI `tests/probe_trace_two_host_owner_cli.py` requires `ASSISTX_TWOHOST_RESEARCH_ONLY=yes-disposable`, explicit pinned identity, and exact observed hostname (`x1-370` or `xwing`). No new HTTP listener, server port, production service, host firewall rule, Docker container, graph query, or credentials. The client `tests/probe_trace_two_host_client.py` checks existing known SSH host key, disables password prompts, bounds SSH connection attempts and timeouts, and issues at most 3 simultaneous SSH calls in a 10-request wave. Failed SSH returns **authority-unavailable**, never local bootstrap or failover.

**This is ONE SQLite authority accessed remotely over SSH, not a distributed consensus/quorum authority.** A passwordless shell login into x1-370 is not a hardened production authorization boundary. Its source SHA is research evidence, not a deployment image.

## Physical two-origin experiment A — shared authority

1. Bootstrap an empty capacity-one owner **only on x1-370**, epoch `9db4f73e-9274-4b8c-8f93-09a632b2695c`, synthetic graph ID `a` × 64, research path `/tmp/assistx-twohost-authority-test-20261009-fleet/authority-test.sqlite`.
2. Concurrent origin attempts: local x1-370 acquired sequence **1**; xwing requested through **xwing → authenticated SSH → x1-370** and received **full**. No overlapping grants.
3. With this first token still occupied, execute bounded requests from **each** physical host: 1/3/5/10 request waves, max three in parallel for each origin. Results: **38 attempted successors, 0 admitted, 38 full denials**, 19 per origin. These are actual SSH request origins, with a single local owner process per incoming SSH request; *not* distributed replicated state or a real Neo4j workload.

## Experiment B — blocked authority delivery

From xwing, run ten remote admissions through a deliberately failed SSH ProxyCommand (`/bin/false`). This simulates **SSH delivery failure** without changing either node's networking. **10 unavailable, 0 full, 0 admitted**. x1-370 subsequently reported `active=1,capacity=1,sequence=1`.

This is a **controlled connection refusal, not a full network partition or management-plane outage**. The test verifies the client does not fall back to copied local state.

## Experiment C — *real split-brain counterexample* with two independent files

Bootstrap a new empty owner on x1-370, epoch `1f96e41f-95f6-4c2b-9a48-f34ff9231a74`. Transfer that 28,672-byte, mode-0600 **empty SQLite database** to xwing using authenticated SCP (no production data). Both nodes then execute the same research CLI against their respective **local** copies, with matching source, pinned epoch, graph ID, schema, and cap=1.

- **x1-370:** `admitted`, sequence 1, token `bf3c…`.
- **xwing:** `admitted`, sequence 1, distinct token `a301…`.
- **Combined:** TWO physical host grants for advertised single fleet capacity. That falsifies any claim that signed/typed epochs or individual SQLite transactions alone make replicated copies safe. **A host may not promote a copy into independent authority during a partition.**

## Experiment D — rollback and independently observed sequence watermark

Bootstrap one more separate research authority on x1-370, epoch `4bfe1f0d-33ab-4a8e-968a-cc17ba4e5a2a`; snapshot its zero-reservation file **before** admission. Admit first request (sequence **1**). From xwing, independently observe `active=1,sequence=1` via the authenticated SSH channel and retain an offline synthetic checkpoint recording epoch/sequence on xwing. Then restore the original empty file into the same x1-370 inode, with **no active workload**:
- Request from xwing requiring the already observed **minimum sequence 1**: **authority-unavailable** due to `OWNER_EPOCH_GRAPH_OR_CHECKPOINT_UNTRUSTED`, no grant.
- A separate *unsafe* request without the current independent watermark, `minimum-sequence=0`: **admitted** on the rolled-back journal, reusing sequence 1.

This demonstrates how an independently pinned sequence can detect the tested rollback and how a **stale checkpoint fails**. The synthetic checkpoint on xwing is *not* trusted PKI, consensus, or a tamper-resistant monotonic counter; production must not delegate the minimum sequence to an arbitrary requester.

## Source and test evidence

- `src/assistx/trace_two_host_authority_research.py`
- `tests/probe_trace_two_host_owner_cli.py`
- `tests/probe_trace_two_host_client.py`
- `tests/test_trace_two_host_authority_research.py`
- `tests/test_trace_two_host_client_research.py`
- Native x1-370: **49/49 PASS** across two-host authority, SSH refusal, and preceding receipt custody tests.
- Native xwing: **20/20 PASS** for two-host authority and SSH refusal, on the exact same fetched source.
- Exact-head GitHub synthetic research workflow includes both new suites; no GitHub workflow will claim to rerun physical SSH experiments.
- Generated SQLite fixture files and the xwing checkpoint are **disposable**, not production state. No key, credentials, or customer data are embedded. Source code does not invoke any admission release.

## Hard release blockers — #148 OPEN

1. **Durable external authoritative epoch/sequence and recipient fencing:** a single SSH/SQLite owner has a SPOF; independent copies admit concurrently. A true quorum or consensus service must refuse stale owners, partitions, rollback and epoch change with authenticated admission tokens bound to physical Neo4j query lifecycle.
2. **Durable receiver trust and consumed-nonce authority:** previous PR #224 can pin a receiver key and preregister expected nonce locally, but has no protected global consumed registry, KMS/PKI-backed signer lifecycle, or evidence of Neo4j truth under graph failover.
3. **Authenticated multiworker physical graph staging:** this two-host slice submits admission *proposals*, **not real Neo4j reads**, so it cannot prove that an old physical query never overlaps a new one. Need x1/xwing authenticated multiworker + independent Neo4j monitor under actual store partition/rollback and long Bolt/management blackout, plus driver p95/p99 and rollback.
4. **Ingress #149 and release:** trusted Caddy/Tailscale header provenance, PII/role scope, release approval and hosted CI must be separately met.

**Disposition:** one surviving authority safely denied all observed duplicate origin requests and transport loss. Replicated-file and stale-checkpoint failover were **falsified on two real nodes**. Research PR remains **DRAFT / NOT PRODUCTION AUTHORITY**.
