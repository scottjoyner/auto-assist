# Issue #118 — NAS ready receipt and legacy ACK release gate

Date: 2026-10-09, x1-370. **RESEARCH-ONLY PATCH; PRODUCTION DEPLOYMENT HOLD.**

## Finding confirmed against the deployed source

The active /home/scott/bin/trace-spool-drain.sh wrapper executes
/home/scott/git/ssd4tb-nas-offload/trace_drainer_safe.py
(frozen original SHA256 c2759e93f8d4e201153e92c04e93933b621ed41d153c128f79472c7cc63f726b).

The deployed publish_one() publishes NAS payload and manifest, then creates local
safe-trace-nas-ack/v1 without publishing a NAS ready marker. Four recently acknowledged
NAS segments were sampled using existence checks only: 4/4 have payload+manifest,
0/4 have remote .ready, 4/4 retain local .ready. A bounded read-only inspection
of all 173 local ACK JSON files found **173/173 without ready_sha256**.
These are incomplete three-part remote receipt attestations, NOT proof the payloads
are corrupt or deleted. No NAS archive contents were read in this check.

## Isolated fail-closed candidate

Local research directory:
 /home/scott/git/delm-sandbox/trace-drainer-review-20261009/

Files:
- trace_drainer_safe.py: isolated copy of deployed module with ready receipt protection.
- three_part_ready_ack.patch: apply against exactly the SHA256-pinned deployed original.
- tests/test_trace_drainer_safe.py: 11 synthetic tests against temporary filesystem.
- patch-replay-acceptance.log: fresh patch applied to byte-identical original, 11/11 pass.

Changes:
1. Require exact local .ready marker to equal the pinned source archive digest.
2. Publish remote .ready LAST, only after archive+manifest staging/publish/read-back.
3. Check remote ready marker presence and digest before a local ACK, including
   the already-present NAS segment path. Never overwrite a conflicting marker.
4. Record ready_sha256 in newly-created local ACKs; reject old ACKs lacking this
   proof rather than treating them as authenticated three-part custody.
5. An interrupted ready upload or collision leaves all local originals intact
   and issues no new ACK. Partial NAS state remains for owner reconciliation.

Baseline 4/4 original synthetic suite PASS.
The three new ready-receipt cases FAIL against the pinned deployed original.
Isolated corrected candidate: 11/11 PASS. Fresh patch replay: 11/11 PASS.

## Safety and rollout gates

**DO NOT DEPLOY THIS PATCH over the active drainer.** Since 173 existing local ACKs
lack ready_sha256, the strict candidate will HOLD on the first legacy ACK until
owner-reviewed reconciliation. This is intentional and must not be bypassed.

Required source-owner recovery:
- Reconfirm NAS physical UUID, CIFS mount and a fresh storage GO before writes.
- Establish independent NAS payload+manifest read-back against retained local
  content and verify archived/historic location of each ACKed item.
- For an ACK missing remote .ready, decide whether a create-only ready-marker
  repair and per-source ACK attestation is authorized; never infer receipts or
  silently overwrite remote files.
- Test staged recovery with remote filename collision, interrupted publish,
  source rotation and rollback; preserve local originals, all legacy ACKs and
  NAS5 evacuation priority throughout.
- Only then consider promotion from the reviewed patch to a controlled drainer
  maintenance window. Legacy issue #117 full retention remains distinct.

Current safe-drain admission holds under NAS L1 THROTTLE; the drainer requires
strict GO. Latest read-only Beelink NAS5 evacuation sample showed ~104 MB each
read and written over four seconds. It must not be interrupted.

Release decision: draft review ready, production custody repair/activation HOLD.
No live drainer, NAS archive, source, ACK, spool, or collector was modified.

