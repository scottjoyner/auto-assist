# Asymmetric Ed25519 resource-receipt research — still not COMMIT authority

**October 15, 2026 | Fleet Commander x1-370 | DRAFT / NO-GO | No deployment.**

## Question and result

Can a separate verifier authenticate an apparently committed resource effect
using only an externally pinned public key, rather than exposing the same
HMAC secret to both PostgreSQL databases as in draft #383?

**Yes for CPU-local asymmetric signature verification. No for separately
governed signing, durable COMMIT attestation, a receipt-consuming anchor,
or resistance to colluding administrators.** This experiment is deliberately
import-only, read-only and not wired to the PostgreSQL anchor.

## Bounded offline experiment

The script scripts/research-asymmetric-resource-receipt.py accepts only
Ed25519 PUBLIC bytes, an externally expected committed effect, separately
supplied prior high-water, independently issued challenge and clock, and
a resource readback callback. It checks a domain-separated canonical
signed v1 claim with strict resource ID, term, adjacent sequence,
generation, checksum, revision digest, nonce, key epoch and a <=60s
validity window. It reads the resource twice after verifying the signature
and denies noncommitted, malformed, inconsistent or changed effects.

The module NEVER generates or accepts a private signing key and never
executes SQL, uses a network or device, reads a secret, persists a counter,
consumes a nonce, or authorizes a production path. Even a positive result
is named RESEARCH_ASYMMETRIC_OBSERVATION_ONLY.

Tests use ephemeral Ed25519 private keys generated only inside pytest,
and actually COMMIT effect+receipt into a disposable SQLite database under
pytest tmp_path. Verification rereads the database via a new read-only
connection. The lab signs expected evidence in test memory, not in
PostgreSQL or in any production resource.

## Adversarial NO-GO controls

1. **Database-only forgery without the private key is rejected** by the
   pinned Ed25519 verifier. A signed fictional future commit is denied
   when no actual database readback matches.
2. **Collusion still wins:** A cooperating signer and source DBA can
   update effect and receipt rows and sign the fictional generation,
   which yields a research-only consistency result.
3. **Trust-root substitution wins:** The same administrator changing
   the caller's purportedly pinned public key can validate their own
   arbitrary signatures.
4. **External high-water restore permits replay:** Previously verified
   receipts become acceptable again if the only checkpoint is rolled
   back. Repeated pure verification never consumes a challenge.
5. **Effect-time TOCTOU persists:** Direct privileged SQL changes
   the resource after both matching readbacks. Earlier acceptance
   is not a resource-owned ongoing fence.
6. **No PostgreSQL anchor enforcement is built:** This module does not
   add an Ed25519 verifier inside the anchor's atomic PostgreSQL CAS.
   No source-owned hardware trust root, cross-DB atomicity, or
   independent resource side-effect provenance is established.

## Reproduction and next safe proof

Run CPU-only source and disposable SQLite tests:

    PYTHONPATH=src python3 -m pytest -q tests/test_research_asymmetric_resource_receipt.py

The next separately approved experiment requires a signer whose
authority is not merely a privileged mutable database row, an
independently protected public-key trust root, and an external
restore-resistant monotonic anchor. Prove authorized signer/DBA
collusion denial, forged effect, root substitution, anchor restore,
uncertain COMMIT without retry, and term/finalization fencing.

Related drafts include #373 signed fiction, #374 resource transaction,
#377 separate DB instance, #378 shared Docker host administration,
#380 challenge-bound receipt, #382 source-less anchor CAS, #383 shared
HMAC receipt. All remain research-only.

**NO MERGE, NO DEPLOY, NO PRODUCTION GRAPH / CREDENTIAL / NETWORK /
ROUTER FAILOVER / MODEL ADMISSION / IOS RELEASE EFFECTS.**
