# PR #119 — physical negative admission and journal custody handoff (2026-10-08)

**Result:** Read-only, zero-claim physical observation succeeded on both target nodes. This is **not** a deployed authenticated issuer challenge, production API startup proof, independent WORM witness, or production execution authorization.

## Scope, provenance and artifacts

Evidence was collected by pinned, non-mutating scripts from PR #119, executed via existing Tailscale SSH with batch authentication and strict host-key checking. Python bytecode writes were disabled for runtime imports; no worker was restarted, no issuer endpoint was called, no claim or task was created, and no journal, key or NAS file was changed.

**Private evidence location:** `/home/scott/git/.trace-pr119-evidence-20261008/` on the control machine (restricted mode `0700`). Files contain metadata and SHA-256 receipts only, **not key contents or node auth tokens**. Do **not** automatically publish these raw host reports to a public repository.

| Node | Physical metadata report SHA-256 | Offline negative report SHA-256 | Read-only chain report SHA-256 |
| --- | --- | --- | --- |
| xwing | `b7e76e4df5a886667e739148e96698153a8846d5c92940f8f034189cef5a6b74` | `b1e4f5082437758f095ac19087d9fd38d2a520215fabb2aee3b6e35a347e7398` | `f418dc0dd4c94e9673eeae7f54eacfd3e7706263fd9b5d65f99d8412f22b14a7` |
| scotts-macbook-air | `29d751113373d610f0557497530439300ec69a1b8279cad5522a0f3a0833babb` | `a4f34dfd4d1cc481b3dda9e01ae246065c00687a78baedc570964badd20404bc` | `b3bd01e23147665500226493fb79cf0c7d97187430666058ff4e4ca1620db4fb` |

Metadata observed at ~21:53 UTC, offline-denial observed at ~22:01 UTC, independent read-only chain verification observed at ~22:04 UTC on **2026-10-08**.

- Installed `trace_claim_live_executor.py` matches reviewed PR source digest `71a293d1775096f8585e573487479632c7374d68e6df5342fb8a93ceb1e97fa2` on both nodes.
- Installed `trace_execution_adapter.py` matches reviewed PR source digest `5015886d5f38337473c828075a6343b41839d8d978681808d0cff610d52d883a` on both nodes.
- On each node, **both** preflight cases (`both_disabled` and `execution_disabled`) denied with `real_trace_execution_disabled`; audit journal stayed byte-exact and zero claims were issued.
- Offline in-memory verification of the existing journal hash chain was successful on each node. xwing: **12 records**; MacBook Air: **10 records**. Node bindings and recorded journal SHA-256 values match earlier read-only observations.
- Existing shadow audit roots were owner-private `0700`; journal files were `0600`.
- Current release worker wiring **does not** byte-match the reviewed PR: xwing has no matching `src/assistx/fleet_node_agent.py` at the inspected shadow release path, while the Mac has a nonmatching file. Neither release contains `src/assistx/trace_claim_lease_api.py` in the inspected path. These results **block** deployed end-to-end route/worker acceptance; they do not call for blind file copying.
- xwing Tailscale self-identity matched; Mac's host-local Tailscale CLI query was unavailable, although SSH via the separately inventoried Mac tailnet peer succeeded. Retain the distinction.
- Coordinator read-only mount inspection found CIFS `file_mode=0755,dir_mode=0755,nounix,noperm` layered under autofs at `/nas`. **Owner-private NAS offload is blocked.** No mount options were changed and no NAS data was written.
- Key-custody, authenticated issuer, and deployed production worker flags were not independently attested; unobserved is **blocked**, not false or passed.

## Executable acceptance evidence

The review branch contains these reproducible, deny-only, local-audit-friendly utilities:

- `scripts/trace_node_readonly_inventory.py` and `tests/test_trace_node_readonly_inventory.py`
- `scripts/trace_node_offline_negative.py` and `tests/test_trace_node_offline_negative.py`
- `scripts/trace_node_readonly_chain.py` and `tests/test_trace_node_readonly_chain.py`

Their tests proved redaction, refusal of unsafe/symlink paths, pinned source digests, unchanged journals and no promotion authorization. At `7b1ca557c93d655e91b2d7229681fc24ea401054`, **43 targeted local unit tests** passed; broader CI evidence must be confirmed separately at that revision.

## Gate disposition

**Closed at evidence level:** exact `main` vs PR failure attribution (see `TRACE_PR119_CONTROLLED_MAIN_BASELINE_20261008.md`); owner-private physical journal visibility; both installed-executor offline negative preflight cases; byte-matched source verification; physical in-memory receipt chain integrity.

**Remains blocked:** direct authenticated physical denied claim; real revocation/renewal fence; deployed issuer/API startup and auth; receiver-owned key custody, provisioning/rotation/escrow; node worker source drift and deployment; independent durable WORM custody; two-phase NAS recovery, owner-private CIFS ACLs and actual fsync/restore; aggregate green CI or explicit authorized exception. Keep both activation flags off and PR #119 draft.

## Next safety-preserving implementation slice

1. Produce a staging issuer **startup/read-only route registration** proof in an isolated environment, without the API lifespan scheduler, live Neo4j claims, or production keys; require observed 503 default-disabled behavior.
2. Reconcile the deployed worker **source revision and signed release manifest** on each physical node before starting any worker/issuer service. Do not overwrite existing releases.
3. Design and independently review receiver-owned signing-key custody, key rotation/revocation and negative issuer behavior with a dedicated staging key; never include a private key in logs/artifacts.
4. Resolve private server-side ACLs on a **separate trace archive destination**, not NAS5 recovery storage; prove independent WORM witness and interrupted-write recovery prior to any offload writes.
5. Only after those gates may a separately approved, typed `probe.noop.v1` authenticated two-node canary be considered. No generic shell, LLM, script or unrestricted agent dispatch.
