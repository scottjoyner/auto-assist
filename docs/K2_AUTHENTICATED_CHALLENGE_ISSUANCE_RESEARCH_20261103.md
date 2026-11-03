# K2 authenticated challenge issuance — synthetic operator/issuer contract

November 3, 2026. Research-only. Stacked above draft PR #495.
No production signer, operator key, external CAS service, private trace,
canonical approval, model load, or runtime routing change is included.

## Why this was needed

The preceding witness verifier and nonce/high-water fixtures proved that
signed physical observations, boot/process fences and a challenge-spend CAS
can fail closed. They did NOT establish the provenance of the nonce itself.
The earlier test ledger exposed issue_for_test(nonce,...) to test code
without a separately authenticated operator-signed issuance request.

The added tests/fixtures/k2_authenticated_issuer_fixture.py implements
a STRICTLY disposable signature+CAS protocol, modeled after the fleet's
EXP-053 operator-authenticated external high-water research. This is
NOT a production integration with the EXP-053 signer or backend.

## Exact request and response

The operator-signed request includes EXACTLY:
- synthetic request schema, node=x1-370, purpose=k2-physical-witness
- 32-byte lowercase-hex challenge and claimed boot-ID SHA-256
- issued_at_ms, expires_at_ms and expected monotonic sequence
- canonical Base64 32-byte operator Ed25519 public key
- canonical Base64 64-byte Ed25519 signature over DOMAIN-PREFIXED
  canonical UTF-8 JSON of all fields except that signature

Issuer verification rejects oversized, noncanonical or duplicate-key
JSON; unknown fields; missing/malformed/wrong-domain signatures;
unauthorized operator keys; wrong node/purpose; invalid clock windows
and boolean/nonpositive sequence values BEFORE any SQLite write.
The operator key SHA-256 is a test-pinned trusted INPUT to the issuer,
not selected from the request or an environment variable.

Under a SQLite BEGIN IMMEDIATE transaction, the issuer requires an
exact expected_sequence = current + 1, distinct nonce and distinct
request hash. It then atomically records the challenge/sequence and
advances the monotonic high-water before returning a response.

The issuer-signed receipt binds the exact original request SHA-256,
nonce, node, purpose, boot claim, issuance/expiry, sequence and operator
key fingerprint. It is Ed25519-signed under a DIFFERENT domain prefix.
The local test receipt verifier independently checks the operator
authorization and the issuer's separately pinned test public key.
Receipt validation strictly preserves can_dispatch=false,
operator_approval_verified=false, independent_custody_verified=false
and decision=HOLD. A signed issuer receipt is not model admission.

An optional test-only enrollment helper calls the original SQLite
challenge fixture's issue_for_test ONLY AFTER successfully validating
the authenticated issuer receipt. The original challenge fixture and
the production witness verifier remain separate. No route, network
listener, credential or DB migration is installed.

## Negative and concurrency evidence

The 32 new focused tests include:
- valid request, CAS and signed response, yet no dispatch permission
- unknown/forged/wrong Ed25519 operator, wrong or empty issuer key pin
- signed but invalid node/purpose, malformed nonce, bool sequence,
  stale/future/overlong issue window
- domain confusion, duplicate JSON keys, noncanonical bytes
- duplicate and replayed challenges, fresh second CAS sequence
- eight concurrent issuers: exactly one successful issuance
- successful external issue with lost acknowledgement: no retry success
- forged/mismatched receipt, incorrect sequence and wrong nonce
- missing local receipt, duplicate local enrollment, damaged highwater
- symlink and existing-file refusal
- restoring old issuer database reissues a previously signed challenge
- restoring BOTH test databases permits repeat enrollment

The final two cases are intentional FAILURES of the security
assumption that a test-owned SQLite file with an Ed25519 signature
provides independent custody. These tests succeed at demonstrating
the attack. All tested responses remain non-admitting.

## Remaining real admission requirements

1. Name and authorize a DISTINCT operator administration/trust domain,
   approve request and issuer code/hardware digests, and independently
   pin the operator/issuer keys. Shared fleet SSH credentials do NOT
   establish independent custody.
2. Place nonce issuance and high-water outside the mutable runtime
   host/fleet-admin domain, with externally anchored anti-rollback
   sequence, custody controls, credential rotation and recovery.
3. Bind the original operator authorization to a uniquely identified
   request, exact model artifact/runtime binary/boot/process inventory
   and final execution-time physical socket/residency verification.
4. Verify the signed issuer response and externally committed spend
   before any canonical signed operator CAS. Unknown outcome must HOLD,
   never automatically retry and mint new authority.
5. Requalify LM Studio version and actual model residency separately;
   the current x1 resident count is zero and canonical generation 896
   was expired at the latest read-only check.

**This module is deliberately test-only and non-authorizing.**
