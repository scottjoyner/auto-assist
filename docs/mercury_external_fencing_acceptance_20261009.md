# Mercury: external mock dispatch fencing across two physical hosts

**Date/time:** Friday, October 9, 2026, 17:10–17:16 EDT (America/New_York).
**Execution:** Fleet Commander on physical `x1-370` and SSH-orchestrated physical client `xwing`, signed request/reply messages over Tailscale.
**Artifacts:** self-contained Python stdlib experiment; separate from cumulative Mercury v4 patch in [auto-assist PR #232](https://github.com/scottjoyner/auto-assist/pull/232) and prior physical negative [PR #239](https://github.com/scottjoyner/auto-assist/pull/239).
**Status:** draft research only, no real model dispatch, no provider API credentials, no production daemon, no NAS mutation or saved real secrets.

## Architecture tested

`integrations/mercury/research/fenced_dispatch_probe.py` implements a deliberately standalone, **mock-only** decision endpoint. The proxy owns a separate SQLite DB for `(generation, issuer)`, not the coordinator DB copied in PR #239. A local compare-and-swap `promote` operation advances the generation and changes the approved issuer. It has **no network-facing promotion endpoint**.

For every `mock_dispatch`, the proxy authenticates a disposable node HMAC signature; verifies unique nonce, job ID, bot, provider, model and upstream group; and compares the requesting issuer and fencing generation against **its own** separate durable state. It stores a decision record and hash-linked audit event. The caller validates the signed reply and nonce. No external HTTP call to any model is made.

The transport is **plaintext HTTP bound to a Tailscale IP**, not TLS/mTLS. The HMAC identities here are random *test credentials*, destroyed after testing. The proxy is a single surviving authority, **not** an HA/consensus cluster, replicated registry, production credential vault or sandbox.

## Witness timeline and results

| Local clock (EDT) | Physical observation |
| --- | --- |
| **17:10:47** | Re-checked both physical nodes, original PR #239, test-port status and free scratch capacity. |
| **17:11:21** | Created isolated auto-assist worktree; no changes to pinned live Mercury checkout. |
| **17:12:58** | First **10/10** offline stdlib unit tests passed for signed identity, replay, exact allowlist, promotion, restart and hash-chain corruption. |
| **17:13:29–17:13:46** | Generated disposable test keys and staged the signed request client on physical `xwing`. |
| **17:13:55** | With proxy generation **0** and no issuer, both physical clients' mock dispatch requests were **denied**. |
| **17:14:05** | Local operator promoted `node-x1` to fence **1**. `x1-370` request accepted, `xwing` requests claiming fence **3** and **1** denied. Source of claimed generation, including copied coordinator state, cannot self-promote the proxy. |
| **17:14:18** | Explicit local operator promotion switched to `node-xwing`, generation **2**. Prior `x1-370` fence 1 denied; speculative xwing fence 3 denied; xwing fence 2 accepted. Attempt to promote with stale expected generation 1 raised `fencing_compare_and_swap_failed`. |
| **17:14:35** | Stopped the test proxy; physical `xwing` request returned `unavailable_denied`. Restarted with identical separate SQLite DB: approved `node-xwing` fence **2** persisted, old x1 fence 1 still denied, valid xwing fence 2 permitted. |
| **17:15:04** | Expanded **11/11** offline tests passed with resource warnings treated as errors. Added deliberate *proxy DB copy* negative: independently cloned proxy DB can issue duplicate mock authorizations. |
| **17:15:30** | Stopped listener. Exported and verified **12** hash-linked events: 3 permitted mock dispatches, 7 denials, 2 local promotions. |
| **17:15:46** | Deleted the ephemeral test config and node keys from `x1-370`; deleted staged node key from `xwing`. Verified no listener on disposable port 18745. |

## Audit evidence and custody

`docs/evidence/mercury-external-fence-events-20261009.jsonl` contains only synthetic decision evidence, exported through `export_fence_audit.py` after recomputing the SHA-256 audit chain.

- **12 events**; proxy generation **2**, issuer `node-xwing`.
- Final verified audit digest: `8703468a4d0c2f5f6375e148de1c976115554d057935a3da5f03961a4df1b6fc`.
- JSONL SHA-256: `21369e7c19f1a4cf0d5bb81ce4dc89001e2cdc86642c33d8734310bc019841ea`.
- Disposable DB retained privately at `/tmp/mercury-fence-20261009-8Y8Jyd/proxy.sqlite` on `x1-370`; not backed up to or cleansed from NAS.
- **No independent append-only receipt service, WORM store or recovery witness exists.** The SHA-256 chain can be rewritten by an actor who controls SQLite; the committed JSONL is a reproducible snapshot, not a security guarantee.

## Main design finding

**PASS, conditional:** as long as all mock dispatches go through **one independent proxy state store**, the copied-issuer epoch from PR #239 cannot grant dispatch. Replacing the approved issuer requires an explicit change *at that independent downstream gate*, and the previous issuer's generation is rejected after transfer.

**FAIL, deliberate negative:** copying the **downstream proxy's own** DB creates another issuer gate that can independently authorize the same fixture dispatch. The new fixture therefore does **not** prove global, non-forkable authority; a future design needs a genuinely external fenced/consensus-backed state source or an isolated, uniquely controlled credential owner. Provider credentials must never be present outside that gate.

**Also unproven:** already-running vendor requests might continue after a generation change; the fixture has no vendor transport, cancellation acknowledgement, kill/timeout proof, usage receipt or enforced one-concurrent-live-model-call capacity. Cross-host connectivity uses SSH for orchestration but the *HMAC client call* was direct Tailscale HTTP. Production transport encryption, a trusted identity source, quorum, independent key rotation and signed custody remain missing.

## Reproduce locally, offline

```bash
cd integrations/mercury/research
PYTHONWARNINGS=error python3 -m unittest -v test_fenced_dispatch_probe.py
python3 -m py_compile fenced_dispatch_probe.py export_fence_audit.py
```

Eleven tests run without opening a network port or importing a provider SDK. Any further physical run must generate **fresh** throwaway keys, new private scratch SQLite and a new bounded port. The production Mercury startup gate introduced in PR #232 is unchanged and intentionally refuses fleet mode.

## Next actionable gate, order matters

1. **P0 — non-forkable downstream authority**: select an isolated credential proxy/consensus store with stable issuer identity, CAS or quorate generation, explicit lease/dispatch capacity and a denied partition behavior. Test that copying a snapshot *of the downstream authority itself* cannot authorize a second real dispatch.
2. **P0 — physical in-flight revocation**: long-running fake model calls; fence rotation while old call is running; require terminate-or-quarantine proof before a different issuer can dispatch, rather than merely denying future requests.
3. **P0 — remote Mercury integration**: scoped `BotManager` requests must traverse a verified proxy with no vendor keys elsewhere; exclude bypasses from raw HTTP, plugins and alternate SDK clients; no live service startup until authenticated authority exists.
4. **P0 — independent trace witness**: archive signed, replayable receipts to independently controlled storage and verify restore under storage pressure. Do not use constrained NAS5 capacity or mutate NAS recovery as a shortcut.
5. **Release** only after exact-head CI, free-provider account/physical-upstream alias/credit verification, independent human operator acceptance and one authorized bounded canary. **No live hosted inference or paid fallback approved.**
