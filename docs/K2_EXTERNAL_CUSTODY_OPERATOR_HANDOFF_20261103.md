# K2 witness external-custody operator handoff — incomplete / NO-GO

November 3, 2026. NO independent operator appointed, no production key
provisioned, no protected external CAS, no production dispatch.

This is a precise request for separately administered evidence,
not authorization to generate keys, modify hosts, change graph state,
deploy services or run hardware attestation commands.

## Read-only fleet baseline

The fixed-source SSH preflight observed destroyer and xwing with TPM
devices visible, but tpm2_getcap missing. Lenovo has no exposed TPM.
All three have synchronized clocks, but none has demonstrated TPM
quotes, protected keys or independent signing custody. All three
were accessed using the existing shared fleet administrator account.

x1-370 LM Studio API was HTTP 200 with zero resident K2 models;
installed LM Studio 0.4.28+2 differs from reviewed 0.4.26+4.
Auto-Router is degraded, free hosted providers zero and canonical
approval generation 896 is expired with no eligible entities.

These facts are operational baseline, not a production admission.

## Five separately custodied evidence artifacts REQUIRED

1. Administration boundary: distinct accountable operator/custodian,
   separated account/credentials, approvals and independent audit logs.
2. Protected signing keys: provenance, public fingerprints,
   protected handles, source approval, rotation and revocation record.
3. External high-water: independently administered monotonic CAS,
   tamper-evident receipt chain, durable fork/rollback protections.
4. Recovery/rotation: signed evidence for unknown outcomes, backups,
   rollback refusal, dual-party handoff and historical key validation.
5. Hardware/runtime attestation policy: independently reviewed exact
   executable/model/boot/socket identity; TPM quote/PCR verification
   only when actually implemented. Mere device visibility is not proof.

Do NOT commit raw evidence containing private topology, device identifiers,
credentials or internal signing keys. Only externally approved SHA-256
evidence references belong in the review claims packet. This verifier
does not fetch or authenticate the five underlying documents.

## Non-authorizing signed claims format

Run the offline verifier with an external review nonce (never obtained
from the packet under review):

    python3 -B scripts/verify-k2-custody-handoff-offline.py \
        --expected-challenge <64 lowercase hex chars> < packet.json

Exact canonical JSON schema: assistx.k2-external-custody-claims.v1.
Request fields are:
- node: x1-370
- model_id: k2-horizon-0.9b
- model_sha256: exact reviewed K2 model SHA-256
- challenge_hex, issued_at_ms and expires_at_ms
- custody_evidence: exactly five SHA-256 evidence-reference claims
- attestations: operator and custodian each with canonical Base64
  Ed25519 public_key_b64 and signature_b64

Each signature authenticates the identical unsigned packet, with its
own role-bound domain prefix. The public keys must match separately
reviewed source pins. Both production pins remain deliberately EMPTY.
A packet cannot select its own trust roots.

The parser refuses duplicate fields, noncanonical JSON, stale/future
or overlong windows, mismatched model/node/nonce, unapproved or
identical signing role keys, signature failures, invalid evidence
digests, or extra fields claiming approval or dispatch.

Even when both signatures validate in TEST fixtures, the response
authenticates only the claims:
- operator_signature_valid=true, custodian_signature_valid=true
- evidence_hash_claims_present=true
- evidence_contents_independently_verified=false
- independent_administration_verified=false
- protected_key_custody_verified=false
- external_antirollback_verified=false
- external_challenge_consumed_once=false
- hardware_attestation_verified=false
- canonical_operator_approval_verified=false
- can_dispatch=false, decision=HOLD

The tool exits nonzero even when claims are signed correctly.
It is NOT a production signer, independent evidence validator,
hardware quote verifier, replay ledger, or authorization to dispatch.

## Exit conditions requiring separate approval

- A genuinely separate operator and custodian review and approve
  the real signed raw evidence, key sources and durable CAS custody.
- Authenticate actual production hardware/host identity, source
  digests, and rollback/fork/recovery procedures.
- Requalify the installed LM Studio binary, verify mapped GGUF,
  real runtime+proxy PID/start-ticks, socket ownership and actual
  loaded resident model on all private access paths.
- Run the independently controlled signed challenge/spend with
  a fresh source-approved execution-time process check.
- Approve fresh expiring canonical operator-signed graph generation
  and maintenance-window/rollback procedures explicitly.
- Measure useful inference/token utilization without bypassing
  admitted runtime/model/path identity or paying for APIs.

The packet is a review artifact ONLY: DRAFT, production HOLD.
No automatic promotion, merging, signing key setup or model loading.
