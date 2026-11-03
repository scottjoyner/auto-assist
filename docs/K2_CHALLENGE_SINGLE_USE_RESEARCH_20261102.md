# K2 signed-witness challenge replay: atomic offline fixture

2026-11-02. Research-only. No independent issuer or external CAS deployed.
No live model, service, signed authority, Neo4j, private traces or paid
provider is used.

## Gap after process and boot identity fencing

PRs #488, #493 and #495 establish local signed K2 observation contract
tests, independently pinned executable/boot identity gates, and a
fresh but presently unconfigured independent current process inventory.
They correctly return HOLD and cannot authorize model dispatch.

They previously reported challenge_bound=true when an Ed25519 receipt
carried the caller-expected fresh nonce. This proves only that the
signature covers the provided bytes. It does NOT prove the nonce was
independently issued or that the same signed receipt has not already
been accepted in a different session or process.

The offline verifier now explicitly returns:
    nonce_one_time_consumption_verified = false
    decision = HOLD
    can_dispatch = false

## Synthetic atomic challenge-spend contract

The new test-only SyntheticChallengeLedger lives under tests/fixtures.
It is not imported by, and cannot enable, the offline verifier service.

A challenge must be enrolled first with its 32-byte lower-case hex
nonce and bounded issuance/expiry. The test fixture creates only a
brand-new SQLite database in a disposable test directory (O_EXCL;
refuses existing files and symlinks). It uses WAL, FULL synchronous
durability, a unique nonce primary key, and a monotonic global
high-water counter.

The spend path first calls the actual offline signature and witness
contract verifier and requires its non-admitting HOLD response with
signature_valid=true and nonce_one_time_consumption_verified=false.
An invalid signature never burns a nonce. It then locks the database
with BEGIN IMMEDIATE, checks that the challenge was enrolled before
receipt signing and that its expiry covers the receipt and current
time, and atomically records the spent nonce, receipt SHA-256 and
monotonic sequence before COMMIT.

Failed or racing spends roll back; a committed spend cannot be
repeated, including after reopening the database or losing the
acknowledgement. The returned test result deliberately remains
independent_custody_verified=false, operator_approval_verified=false,
decision=HOLD, can_dispatch=false.

This is a test model for transaction semantics, NOT trusted
off-host custody. An attacker with write/restore access to the same
SQLite database could roll it back or replace it. An externally
authenticated CAS response and independently anchored high-water
chain are still absent.

## Regression evidence

The combined test suite includes 74 earlier physical/signature/
boot/current-process tests and 18 new challenge-spend tests: exact
once-only consumption, eight competing threads with one winner,
database reopen and a separate interpreter readback, durable
unknown-outcome retry refusal, mismatched or unissued challenges,
duplicate issuance, expiry/counter corruption, forged signature
with no spend, two independent challenges advancing the counter,
filesystem symlink/existing-file safeguards and refusal to treat
boolean-only verification as trustworthy.

92 local tests passed with strict Ruff and compilation.

The scoped hosted workflow runs the complete combined suite and
confirms all independent witness/process/boot trust pins remain empty.
The production signer and nonce issuance keys are not created.

## Mandatory remaining architecture

Before any real challenge may be spent for a production admission:
1. A separately administered issuer must generate and register a
   fresh random nonce and bind it to an authorized request.
2. External CAS must authenticate the issuing operator and signed
   witness response, and independently verify signer/counter/receipt.
3. Protect durable high-water, anti-rollback and key-rotation custody,
   publishing signed spent receipts verified before graph changes.
4. Couple transitions to execution-time boot, PID, socket ownership,
   K2 model residency and an approved canonical generation.
5. Refuse interrupted CAS without automatically minting new authority.

The actual x1 K2 resident count remains zero and canonical
generation 896 has expired. Production HOLD.

## Synthetic separate high-water / rollback experiment

The expanded offline suite uses a SECOND disposable high-water database
in the test directory. It maintains a nonce-spend registry and a
monotonic sequence, separate from the local challenge ledger. It issues
a test-only Ed25519 signature on a tightly structured acknowledgement
binding the nonce, verified receipt SHA-256, and next sequence.
The local fixture checks that acknowledgement's signature against a
synthetic separately pinned public key before committing its local spend.

In these tests the external reservation is committed FIRST, then the
local database commits. If the external acknowledgement is lost or
invalid after the reservation, the local nonce is not spent; a future
attempt is denied because the external counter has already advanced.
This is intentional fail-closed behavior: availability may suffer, but
an uncertain transaction must not create duplicate authority.

The adversarial tests demonstrate:

- A standalone challenge database can be rolled back and reuse a nonce.
- Keeping the second high-water store intact detects local rollback.
- Restoring the external store alone prevents the next high-water
  transition because its sequence disagrees with the local ledger.
- A lost acknowledgement and a corrupt, forged, unsigned, or wrongly
  pinned acknowledgement never authorize a local transition.
- Eight competing consumers still have exactly one successful spend.
- Restoring BOTH databases reproduces the replay even when the
  synthetic acknowledgement is correctly signed.

The last case is a deliberate proof of the UNSOLVED custody issue.
Both databases and the test signing key are administered by the same
disposable test runner. They are NOT independently controlled or
rollback-resistant. A signature only authenticates the test key holder;
it does not make local files an independent external CAS.

The newer tests do not modify the actual K2 verifier's authority:
all trusted production signer, runtime, boot, and process pins remain
empty or unconfigured, all spent acknowledgement outputs report HOLD,
and no real high-water backend, network endpoint or signing service
is installed.

The combined physical witness, signature verification, nonce CAS and
dual-ledger rollback suite now contains 109 synthetic tests. Release
still requires an independently administered signed witness/CAS,
operator-authenticated issuance, anti-rollback outside shared fleet
administration, approved rotation and recovery, and binding of the
signed result to the execution-time model and canonical graph gate.
