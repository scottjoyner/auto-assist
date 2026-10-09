# Mercury: in-flight uncertainty quarantine across physical x1-370 and xwing

**Experiment:** 2026-10-09, approximately **17:47–17:54 EDT** (America/New_York). `x1-370` was the experimental in-memory mock vendor-work coordinator; `xwing` was a separately running physical client and eventually a physical copied authority.
**Status:** research only. No Mercury process deployed, no hosted inference, no real provider request or credential, no paid fallback, no production SSH daemon changes, no NAS write or deletion.
**Dependency:** [PR #232](https://github.com/scottjoyner/auto-assist/pull/232) (signed mock Mercury SDK gateway); [PR #239](https://github.com/scottjoyner/auto-assist/pull/239) (physical copied SQLite authority split brain); [PR #240](https://github.com/scottjoyner/auto-assist/pull/240) (separate downstream mock fence, itself forkable).
**Source:** `integrations/mercury/research/inflight_gate.py`, executable independent Python 3 standard-library-only fixture, **NOT** an upstream Mercury patch and **NOT** real model streaming/transport.

## Tested protocol

The one surviving fixture authority maintains an independent SQLite `controller(generation,issuer)` and exact `slots(upstreamGroup,runId,job,node,generation,state,quarantinedAt)` table. Every request has test-only per-node HMAC, nonce anti-replay, exact bot/provider/model/physical upstream-group allowlist, and a signed response. A local operator `promote --expect` CAS changes issuer/fence generation.

**Key rule:** promotion while an earlier mock operation remains active changes its slot state to **quarantined** rather than dropping or transferring it. Quarantined work blocks all new starts, even for the correct newer issuer. Old issuer `finish` is stale; an old worker's `cancel_ack` is **not** independently trustworthy termination evidence and does not release the seat.

A synthetic local `witness` command, using a **separate disposable signing key that was never copied to xwing**, can issue a fake `mock_worker_confirmed_stopped` receipt. The local `reconcile` command checks its signature, exact run ID, current generation, timestamp after quarantine and one-time nonce before freeing the slot. **This demonstrates only validation/reconciliation mechanics; the operator can mint the witness, and there is no actual provider termination signal, independent process attestation, vendor cancellation or independently trusted witness.**

On restart, any previously `active` slot is immediately marked `quarantined`. Quarantined slots never expire automatically; this deliberately favors blocking over unsafe reuse.

## Physical test timeline — Friday October 9, EDT

| Time EDT | Physical observation |
| --- | --- |
| **17:47:22** | Confirmed x1/xwing connectivity, PR #240 clean, previous test ports unused, scratch storage available. |
| **17:47:42–17:50:42** | Built isolated research-only in-flight gate and **13/13** offline Python tests. Tests cover one-slot capacity, issuer transfer quarantine, stale self-ack, boot recovery, exact signed witness, forged/wrong witnesses, replay, mutation detection and copied gate failure. |
| **17:50:52–17:51:10** | Restricted `/tmp` scratch sessions and throwaway HMAC identities on both hosts; separate synthetic witness key on x1 only. |
| **17:51:18** | Both physical clients denied new work before any issuer was promoted. |
| **17:51:31** | Operator promoted x1 fence 1, then x1 began mock work `physical-inflight-x1`, run `2ffc9b8b-ecb3-4140-a9d1-ebc1c66aba40`. Operator promoted xwing fence 2 while that mock operation was still marked active. Slot persisted as **quarantined**. Xwing with correct fence 2 was **denied** (`capacity_held_or_uncertain`). |
| **17:51:47** | Old x1 `finish` and `cancel_ack` both denied/nonreleasing; xwing still denied, quarantined slot intact. |
| **17:51:59** | Killed test authority; xwing received `unavailable_denied`. Restart of same SQLite state preserved quarantine and rejected new xwing work. |
| **17:52:15** | Wrong-generation synthetic witness failed; valid exact-run/gen-2 synthetic receipt accepted by local operator `reconcile`, then physical xwing began new fixture operation; old slot no longer present. |
| **17:52:53** | Xwing completed its own new mock operation and released slot normally; hash audit intact, no active slot. |
| **17:53:03–17:53:13** | Copied the entire clean gate SQLite DB to physical xwing; independent xwing gate started on distinct Tailscale endpoint with same gen 2. **Both original and copied authority admitted distinct mock operations for identical physical upstream group.** The copied gate's unsafe failover was reproduced on actual physical machines. |
| **17:53:37** | Exported two verified non-secret SHA-256 linked audit histories and matching custody digests. |
| **17:53:48–17:53:57** | Stopped both disposable TCP listeners, verified 18746 and 18747 closed, disposed local and remote test HMAC keys, config and synthetic witness files; original scratch SQLite DBs retained only for local forensic review. |

## Physical negative: local quarantine != distributed fencing

On `x1-370`, the original authority had issuer `node-xwing`, generation **2**, and an empty slot when its SQLite state was copied. The separately started gate on physical `xwing` read the same generation and issuer. An authenticated physical `node-xwing` client then sent one `begin` to each gate. Both returned **mock_started** at generation **2**, yielding two active slots for the same supposed upstream capacity.

This shows a stronger condition for the next slice: **not only the coordinator/issuer, but the final downstream model dispatch or credential owner must be externally unique and uncopyable.** Neither HMAC nor local CAS/SQLite nor quarantine proves the system globally exclusive when the last admission authority is forked.

## Evidence lineage

Both JSONL files were exported only after SHA-256 chain verification, with an explicit whitelist of synthetic fields:

| Source | Events | Final audit head | JSONL SHA-256 |
| --- | --- | --- | --- |
| `x1-370` original authority | **17** | `79e344c0000f8b87b4a1cf73fcd524f96990c8e20f2f02aadbe8f6ec382fb77f` | `dd2bc915fcebd64d0cd3ca58b7390f35d9a99791967ac303fa05c8816ec1f1d5` |
| `xwing` copied authority | **18** | `b9c86372d0ecb245652c374dbf2e4faa724b6c8700e280cb44e71df43a9dd17f` | `e1ba39ae0c22483ca43e1d6302037322f561c27f68efa54d9b0fda4e2e11092d` |

- `docs/evidence/mercury-inflight-x1-original-20261009.jsonl`
- `docs/evidence/mercury-inflight-xwing-copy-20261009.jsonl`
- `integrations/mercury/research/export_inflight_audit.py` can verify/export a *local* source DB.
- Original scratch files on x1 under `/tmp/mercury-inflight-20261009-cHRvZs` and same named xwing `/tmp` folder remain for local research inspection. Do not mistake them for immutable custody or a production recovery archive. Anyone controlling the SQLite DB can rewrite the local hash chain; published Git data is a snapshot.

## Acceptance and rejection matrix

| Requirement | Gate |
| --- | --- |
| One authority enforces one local mock upstream slot | **PASS, fixture** |
| Uncertain active call becomes quarantined on issuer change | **PASS, physically demonstrated** |
| Stale `finish` / self-`cancel_ack` cannot clear quarantine | **PASS, physically demonstrated** |
| Restart/authority outage cannot implicitly clear quarantine | **PASS, physically demonstrated** |
| Exact-run, signed **synthetic** witness must reconcile before capacity reuse | **PASS, fixture semantics only** |
| A copied downstream authority cannot issue overlapping admissions | **FAIL, reproduced on x1 and xwing** |
| Actual LLM stream, cancellation, provider side-effect rejection, usage settlement | **NOT TESTED** |
| Independent real-world termination attestation / WORM trace receipts | **NOT TESTED** |
| Externally unique credential owner, quorum or trusted monotonic fencing | **MISSING** |
| Production Mercury activation or real free-provider canary | **BLOCKED** |

## Reproduce without network or provider transport

```bash
cd integrations/mercury/research
PYTHONWARNINGS=error python3 -m unittest -v test_inflight_gate.py
python3 -m py_compile inflight_gate.py test_inflight_gate.py export_inflight_audit.py
```

Tests: **13/13 passed** under warnings-as-errors on `x1-370`. Fixture does not import SDKs or call model APIs.

## Critical next action for AssistX/Mercury agents

Do **not** loosen the startup refusal from PR #232. Separate what is now known from what remains:

1. Establish a uniquely controlled **credential/dispatch owner** that cannot be reproduced by copying its filesystem or VM image. This may require an externally controlled resource, strong isolated key custody, consensus-backed fencing, and a proxy that refuses stale calls at the actual outbound provider boundary. Do not infer global authority from Tailscale membership or signed DB leases alone.
2. Simulate a **genuinely active fake transport stream** with a separate observer, stop acknowledgement and in-flight side effects; require quarantine until an independently issued termination/side-effect receipt is verified. This fixture's synthetic operator witness is not that.
3. Test stale issuer revocation under physical network partitions and stolen/copied downstream state; measure zero overlapping outbound operations, rollback/cancel outcomes, and operator hold during uncertain results.
4. Keep receipt lineage independent, signed and restorable before using production storage; no NAS5 reformat/delete or write amplification while recovery gates remain blocked.
5. Once all readiness gates pass: reconcile current main/CI and physical upstream/free quota alias account, require explicit operator authorization for one bounded free-model canary. No paid fallback or production failover has been approved.

**Decision: continue research; production remains NO-GO.** This is an acceptance artifact and explicit negative proof, not a deployment request.
