# K2 independently administered witness — read-only custody readiness

November 3, 2026, around 07:11–07:14 EST. DRAFT RESEARCH ONLY.
**NO independent custody established; production still NO-GO.**

## Purpose

The stacked draft K2 external witness studies (PRs #485, #488, #493,
#495, #497) validate offline physical-observation schema, Ed25519
witness signatures, exact model/runtime hashes, boot/process replay
fences, operator-authenticated challenge issuance, synthetic
single-use nonce consumption and high-water rollback failure cases.

Those tests are NOT evidence that a real witness has independent
administration, protected hardware signing keys or rollback-resistant
receipt custody. Shared fleet SSH access to a second computer does
not establish independent control over evidence generated there.

This checker records read-only technical readiness of reviewed
candidate hosts while custody/authority fields remain false.
It is not a witness daemon and does not provision any key.

## Validated on actual fleet

The original pinned EXP-053 off-host preflight was run read-only on
lenovo, destroyer, xwing and returned READ_ONLY_CANDIDATE for each.
It explicitly reported independent administration=false,
operator key custody=false, witness durability=false and
NO_GO_RESEARCH_ONLY.

A fresh fixed-source additional diagnostic using a separate nonce
and the same strict SSH constraints showed:

| Reviewed host | TPM device | TPM sysfs | tpm2_getcap tool | NTP | Outcome |
| --- | --- | --- | --- | --- | --- |
| lenovo | Not exposed | No | Absent | Synced | NO_VISIBLE_TPM_ALTERNATE_CUSTODY_REQUIRED |
| destroyer | Exposed | Yes | Absent | Synced | TPM_TOOLING_NOT_VALIDATED |
| xwing | Exposed | Yes | Absent | Synced | TPM_TOOLING_NOT_VALIDATED |

EFI variable directory and kernel lockdown file visibility were
reported, but neither proves Secure Boot is enabled or measured
boot and protected TPM signing have been established.

All three were reached through existing shared fleet administration.
This does not qualify as independent custody regardless of TPM.

Latest observed x1-370 status: LM Studio HTTP200, K2 loaded count 0,
Auto-Router status=degraded, free hosted provider count 0, root ~226GiB
free. The signed canonical admission remains expired generation 896,
with no eligible runtime, loaded model, transport or capacity records.
None of this checker code changes that gate.

## Reviewed command

    python3 -B scripts/inspect-k2-witness-custody-readiness-readonly.py lenovo destroyer xwing

The command deliberately returns status 2 / NO_GO_RESEARCH_ONLY
even for technically capable hosts. It never treats a successful
probe as authorization. Only three reviewed aliases are accepted.

Read-only actions: short pinned-host-key SSH running fixed Python,
checking presence of TPM devices/sysfs, installed TPM tooling,
time sync, and versions. No TPM command is invoked or hardware
quote attempted; no secrets, file writes, installs, permissions,
services, model inference or routing changes.

Strict fail-closed behaviors: BatchMode=yes,
StrictHostKeyChecking=yes, ForwardAgent=no,
ClearAllForwardings=yes, no SSH control master reuse,
fixed remote source, timeout, bounded response, nonce echo
validation, duplicate key rejection, typed capability fields,
and no printing of remote stderr/private host IDs.
Remote self-assertions of independent custody or dispatch authority
are unexpected fields and explicitly rejected.

## Required independent handoff — NOT satisfied

1. Name a different signing operator, with administration/approval
   and credentials independent of fleet orchestration and x1.
2. Define protected key-generation/provisioning and public fingerprint
   attestation under different custody. Shared fleet-admin SSH cannot
   certify a genuinely external operator key.
3. Independently review source digest, hardware capability and
   measured-boot/TPM quote policy if TPM is selected. TPM device
   visibility is insufficient. Destroyer and xwing have not
   demonstrated TPM quote ability, PCR validation or protected keys.
4. Place nonce issuance, signed spend receipts, durable anti-rollback
   high-water and reviewed recovery/rotation under that custody,
   outside shared fleet controls.
5. Tie approved proofs to execution-time process/socket/model checks
   and valid canonical operator-signed CAS; never promote synthetic
   test returns into production authority.
6. Resolve installed LM Studio 0.4.28+2 vs reviewed 0.4.26+4,
   physical K2 residency and process/transport proof under
   appropriate separately authorized permissions.

Until independently evidenced, production remains HOLD:
can_dispatch=false, independent_key_custody_verified=false,
external_antirollback_verified=false.

## Tests

33 synthetic tests cover reviewed alias allowlist, strict SSH
options, nonce mismatch, duplicate or extra fields, typed booleans,
malformed JSON, time skew, NTP, TPM/tooling gaps, Secure Boot
visibility versus actual proof, and permanent NO-GO for fully
capable hosts. They run alongside 141 prior K2 physical/model,
issuer, signature, replay and rollback tests (174 total).

No signer/operator was identified or enrolled. No production
services, signing keys, TPM operations, model residency, archive
data, approval records or routing were changed.
