# Trace NAS: two-phase signed intent and recovery

**Decision and implementation date:** 2026-10-08 (America/New_York)
**Scope:** isolated AssistX review source; not deployed, not a write to current NAS.

## Problem and prospective acceptance

Current trace custody can persist an encrypted archive and final `index.json`
before it durably appends the *separate* local signed success witness.
A crash at that boundary leaves complete ciphertext on NAS but no proof
whether the controller authorized that publication. The previous publisher
deliberately blocked all such ambiguous recovery attempts. The prediction
for this slice was that a durable signed `PREPARED` intent written **before**
remote bytes, followed by `ACKNOWLEDGED` after the success witness, would
make this transition safely retryable without deleting or rewriting evidence.

Expected acceptance: the same staged source and mount identity recover
after a crash at the index/witness boundary; a crash after the witness but
before ACK adds only the missing ACK, not a duplicate success witness.
Removing the witness **after** ACK must continue to deny; damaged intents,
wrong ciphertext, wrong mount, or node substitution must never be accepted.

## State machine and proof ownership

| State | Signed local evidence | Remote state | Next permitted action |
| --- | --- | --- | --- |
| New | no intent or witness | no complete index | validate source+private dirs+mount, then fsync PREPARED |
| PREPARED | HMAC-signed append-only intent row | zero, partial, or fully indexed archive | no-overwrite resume, decrypt+restore complete bytes |
| Restore verified | PREPARED | matching complete archive | append+fsync published success witness |
| Witness persisted, ACK absent | PREPARED plus valid success witness | full matching archive | append+fsync ACK, no duplicate witness |
| ACKNOWLEDGED | PREPARED+ACK and success witness | complete indexed archive | independent verifier can mark locally witnessed |
| ACK exists but witness missing | PREPARED+ACK | any | DENY: never recreate a previously lost success record |
| Missing PREPARED but indexed remote archive | no intent | complete index | DENY: index alone grants no authority to recover |
| Intent corrupt/torn/incorrect phase | untrustworthy | any | DENY: no journal repair, deletion or reset |

Files:
- `src/assistx/trace_nas_intents.py`: validates signed append-only
  `publication-intents.jsonl` with strict PREPARED → ACKNOWLEDGED state
  transitions, matching node, journal SHA-256, ciphertext index SHA-256,
  CIFS source and target, and previous-signature linkage.
- `src/assistx/trace_segment_nas.py`: opens the existing exclusive local
  publisher lock before inspecting intents; fsyncs PREPARED before any NAS
  publication, indexes remote ciphertext last, re-verifies the restore,
  fsyncs success witness, then fsyncs ACK. A new call may recover a fully
  indexed archive only with a matching unacknowledged PREPARED intent.
  `verify_published_bundle` requires both an authentic success witness
  and a matching ACK, in addition to full independent decrypt+restore.
- `tests/test_trace_segment_nas.py`: crash before witness, crash before
  ACK, damaged intent row and loss of a previously acknowledged witness,
  in addition to mount loss, concurrency and NAS tampering negatives.

## Test observations and limitations

**Offline fixture observations:** the four added crash-window negatives/
recovery tests passed when first run independently. A broader suite is
run against the latest separate AssistX review worktree; report the final
result and CI status without confusing them with a real deployed run.

This adds a *local crash-consistency protocol*, **not external WORM or
rollback resistance**. The HMAC key and ledger are still in controller
custody. An attacker replacing the entire local intent, local success
witness and NAS ciphertext with an older consistent snapshot is not
stopped by this protocol. A separate receiver-owned durable witness,
anti-rollback monotonic sequence anchoring and key custody remain blocked.

The live shared `/nas` mapping at this checkpoint is
`cifs //192.168.1.202/fileserver /nas` beneath a systemd autofs wrapper;
it presents `file_mode=0755,dir_mode=0755,nounix,noperm`. This fails
owner-private NAS archive mode requirements. **No actual archive writes**
or production service changes were performed; do not alter mount options
used by other services, equate it with NAS5 recovery /dev/sdd2, or lift the
7 MiB journal admission cutoff.

## Receiving maintainer's next safe gates

1. Keep PR draft and trace real-execution flags disabled. Do not deploy
   the two-phase publisher on a `0755` CIFS directory.
2. Verify current remote branch / CI; do not overwrite concurrent changes.
3. Provision a dedicated restricted CIFS namespace, check server-side ACLs,
   and measure actual `os.link` no-overwrite and directory fsync behavior
   with bounded disposable encrypted data.
4. Install a **different-custodian** signed WORM/append-only witness before
   treating any local ACK as durable, independently anti-rollback custody.
5. Add a measured scheduled streaming exporter, byte backlog telemetry,
   retention alerts and key rotation. Only after independent restore and
   failure drills pass should trace admission backpressure be reconsidered.

Related: [AssistX PR #119](https://github.com/scottjoyner/auto-assist/pull/119),
[NAS issue #127](https://github.com/scottjoyner/auto-assist/issues/127),
[production gate #120](https://github.com/scottjoyner/auto-assist/issues/120).


## Final local acceptance checkpoint (2026-10-08 evening EDT)

The complete refreshed trace/security/recovery acceptance suite, including
the parallel agent's newer physical read-only inventory, negative-probe and
journal-chain verification tests, passed **241 tests, 1 deprecation warning**
on the review worktree. Ruff and compilation checks passed. The four new
crash-window scenarios passed individually as well. These are offline
fixture and simulated NAS tests, not real CIFS write acceptance or
production-authorized AssistX execution.

**Promotion decision:** retain the 7 MiB admission gate and all production
trace/issuer flags disabled. This protocol improves deterministic safe
recovery, not independent WORM custody. No historical archive, witness,
NAS5 recovery data or running worker was changed.
