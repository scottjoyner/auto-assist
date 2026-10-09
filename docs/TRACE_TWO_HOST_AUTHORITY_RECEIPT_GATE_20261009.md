# #148 two-node authority: receipt and rollback research gate

Date: 2026-10-09 (America/New_York). Disposition: **research only / failover NO-GO**.
Hosts: x1-370 and xwing, existing authenticated SSH. No new listeners.

## Physical observations carried forward

- One disposable SQLite owner on x1-370: two origins contended for
  capacity=1. x1-370 admitted sequence=1; xwing was refused (full).
- Further xwing controlled transport-failure wave: 10 unavailable,
  zero admitted. This was a forced SSH failure, not a true partition.
- Two independent copies of empty owner state (same epoch, graph, schema):
  both admitted a sequence=1 grant against intended fleet capacity=1.
  This is an EXPECTED split-brain safety counterexample.
- Same-inode rollback to a pre-grant snapshot: caller with independently
  observed sequence watermark 1 rejected restored sequence 0; caller
  presenting stale watermark 0 received a new sequence=1 grant.
- Prior disposable databases/checkpoints were removed on both hosts;
  their absence was verified in the previous physical session.

## This follow-on code slice

- The research client now requires an **explicit** minimum sequence.
  Zero may be used to inspect fresh-bootstrap behavior, but is not proof
  of continuity or rollback protection.
- The owner CLI includes pinned epoch and graph identity in its response.
- The research client refuses malformed, missing, wrong-host, wrong-epoch,
  wrong-graph, wrong-sequence, or unrecognized grant responses. It checks
  token/nonce shape but does **not** authenticate those values independently.

## Fresh validation

The same new research tests (including receipt negatives) passed:
- x1-370: **33/33**, Python compile OK, tested from isolated worktree.
- xwing: **33/33**, Python compile OK, tested against the matching base
  source revision using a temporary copied test overlay, then cleaned up.
- These are unit/fixture tests, NOT proof of any consensus implementation.
- Fresh physical bounded smoke with updated receipt client: xwing -> x1-370
  admitted 1/1; x1-370 pinned to sequence=1 then received full 1/1;
  forced SSH transport failure from xwing returned unavailable 1/1 and
  admitted zero. No concurrent independent authority was promoted.
- x1-370's temporary authority directory and xwing's copied client overlay
  were removed, with both paths verified absent.
- Historical copied-state and rollback counterexamples were not repeated
  in the fresh smoke; the previous evidence remains the failover NO-GO basis.

## Remaining acceptance before any distributed failover

1. Add a distinct independently controlled **fencing/ownership authority**
   with one monotonic generation per logical admission domain. A copied
   database or equal epoch must never grant takeover.
2. Make loss of proof/lease immediately stop new admission; test rejected
   stale writer after recovery, partition and suspended-process resume.
3. Atomically bind new admission and external effect execution to the
   current fencing term. A stale process holding an old permit must be
   rejected by the effect boundary, not just by its local coordinator.
4. Make recovery reconcile uncertain in-flight effects and never replay
   them merely because the leader changed. Test restore, duplicated
   requests, release replay, term rotation and witness unavailability.
5. Use separately witnessed monotonic history and test its own rollback
   and availability semantics. A client-supplied floor is not a quorum.
6. Only after isolated negative tests and independent review: authenticate
   production ingress and quantify 1/3/5/10-client resource behavior
   on an approved staging topology, maintaining exact-head CI checks.

## Scope

No production Neo4j, Redis, AssistX runtime, route, graph data, NAS
storage, provider endpoints, or service ports are changed or authorized
by this slice. Do not claim HA, exactly-once effects, or deployability.
