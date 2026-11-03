# K2 external signed physical-witness verifier — 2026-11-02

RESEARCH ONLY. NO INDEPENDENTLY APPROVED WITNESS TRUST ROOT. NO PRODUCTION ADMISSION.

The x1 K2 local observer in draft PR #485 collects process, socket, exact
model bytes and HTTP path evidence but cannot independently sign it or
authorize runtime admission. The fleet's EXP-053 research already establishes
the pattern of Ed25519 request verification with deliberately empty trust
pins, without treating a signature as independent operator custody.

## Proposed detached receipt contract

The offline verifier takes a bounded 64-KiB UTF-8 JSON envelope with
exactly eight fields: schema, node, expected_challenge_hex, issued_at_ms,
expires_at_ms, witness_public_key_b64, observation and signature_b64.
The exact schema is assistx.k2-external-physical-witness.v1, node x1-370.

The signature is Ed25519 over UTF-8 canonical JSON of the seven
non-signature fields (sorted keys, compact separators, no NaN).
The verifier refuses duplicate JSON keys at all depths, unknown fields,
noncanonical Base64, missing/wrong signatures and unapproved signer keys.

The trusted public-key SHA-256 pin is intentionally an EMPTY constant
in this repository and cannot be set by the untrusted envelope or by
the command-line. Only isolated unit tests inject ephemeral keys.
No production keypair is generated, written or deployed.

## Freshness, process and model constraints

The signed receipt must be no more than 120 seconds old, must not be
future-dated, must expire no later than five minutes after issuance,
and must be bound to the exact externally supplied random challenge.
The embedded observation must be no more than 120 seconds old and must
precede signature issuance. Reusing the same operator challenge could
allow replay: a separate durable challenge registry remains required.

Any observation claiming the wrong reviewed application version, or
simultaneously reporting successful identity with an unresolved physical
collection error, is rejected even if its external envelope is signed.

All physical-evidence fields must be exactly true booleans. The embedded
report must remain gate HOLD, can_dispatch false and no self-claimed
operator approval. The report must include a verified actual running
executable, GGUF hash, mapped GGUF, and the exact loopback/LAN/Tailscale
catalog and socket owners under the observed two-tier LM Studio plus
two distinct socat process configuration.

A signature only authenticates the key holder's CLAIMS; it does not
prove independent hardware custody, true model residency or an
operator-approved deployment. Even valid synthetic test signatures
return can_dispatch false and decision HOLD.

## Current fleet evidence

x1-370 model GGUF SHA-256 and size have passed inspection. The shared
LM Studio service advertises zero K2 residents, installed version
0.4.28+2 differs from the reviewed 0.4.26+4 resident pin, and this
Fleet Commander context cannot inspect actual proc executable/socket
file descriptors. Canonical generation 896 remains expired.

No real signed independent physical evidence exists at this time.
Production router stays fail-closed. This PR is only the verifier-side
software boundary. No host privileges, service, DB, model load, key,
real trace or paid endpoint is changed.

## Further independent custody gates

1. An accountable independent operator must approve the distinct witness
   administration, measured observer code, Ed25519 public-key root,
   host access, revocation policy and privilege limits.
2. The witness must not be administered by the measured serving process
   or controller. A remote host with the same fleet administrative
   credentials is NOT independent operator custody.
3. A challenge/counter registry with replay detection and complete
   signed-key rotation and recovery must be independently reviewed.
4. Run actual LM Studio target 64k/4-slot tests, attest model residency
   and physical dual-private-path ownership under the qualified build.
5. Submit independently reviewed candidate evidence through the existing
   signed operator approval and canonical compare-and-swap path. This
   verifier never creates that approval or enables routing.

Draft only: NO GO for production.
