# Hermes witness signed publication grants — October 22, 2026

**Release decision: HOLD.** Draft auto-assist PR #425; P0 issue #414 remains OPEN. This is a research-level owner-specific authorization contract and a separate-host synthetic test, not a deployment of independently administered authorizers or a rollback-resistant authority.

## New exact mutation contract

New `src/assistx/hermes_witness_grants.py` verifies canonical Ed25519-signed authorization grants with exact schema/action/owner, a 32-byte nonce, target generation, exact checkpoint and corpus-manifest SHA-256 digests, and the **precise predecessor generation and digest pair**. Malformed, unsigned, modified, untrusted-key or extraneous-field grants fail closed. The signing key must be independently controlled; the verifier never stores a private issuer key.

The research SQLite witness's new `apply_signed_grant` path verifies that grant and, under a single BEGIN IMMEDIATE transaction, enforces: explicit owner enrollment (prior generation 0 with zero predecessor hashes), strictly advancing subsequent generation, exact durable prior state, a one-use global grant nonce, sticky revocation, and revocation bound to exactly the current owner state. A failed transaction rolls back state and nonce storage together; mutually conflicting signed publications cannot both win. The issuer key must be **pinned and trusted by a separately authorized authority**. A caller-provided key by itself proves nothing. The existing raw `publish` and `revoke` functions remain for explicit offline research fixtures only and MUST NOT be exposed by any real witness service.

## Actual x1-370 to Lenovo verification

The updated `tests/hermes_durable_witness_offhost_canary.py` uses an ephemeral x1-370 owner-authorization Ed25519 signer **held in x1-370 process memory**. The signed grant and its public verification key are sent via existing strictly host-key-verified noninteractive SSH. Lenovo pins that public key in its private *disposable* research directory at enrollment and rejects key substitutions thereafter. Lenovo generates a distinct test-only response signing key, held temporarily in its private disposable directory so separate remote processes can reopen the SQLite ledger. Neither private signer is committed or copied across hosts.

Proven cases: signed initial generation-2 publication; a later signed generation-2 challenge accepted after reopening SQLite; a **forged replacement issuer public key denied**; a correctly signed request to publish an older generation denied; an old checkpoint challenge denied; and a correctly signed revocation retained after further process restart. The research files, keys, temporary SQLite, SSH child processes and UNIX/socket state are destroyed at the end. Production Hermes stays on its exact original image.

Offline regression tests include signature manipulation, cross-owner grants, wrong predecessor, same-generation conflict, nonce replay, duplicate publishers, sticky revocation, forged key, and atomic concurrent signed updates. The worker is still not a reviewed production publisher or client-identity authenticator.

## Critical limits and required production gates

The original SQLite research APIs remain callable by an untrusted process with arbitrary code execution: any real service MUST make only authenticated, authorized grant application reachable. In this research canary, the bootstrap enrollment public key is accepted from the same operator that connects via SSH; there is **no independent approval or immutable trust-root administration**.

A deliberate negative test restores an older complete SQLite database snapshot. This also rolls back the consumed-grant nonce table, allowing a previously applied signed grant to be accepted again. **This is NOT rollback resistance.** Appendix-only/independently audited checkpoints, signer/key separation in an independent administrative domain, revocation/fencing and immutable enrollment policy are still missing. Same-operator Lenovo and x1-370 are not independently controlled.

The production Hermes container still exposes 16 mounts, nine writable, including two writable aliases enclosing protected SSD knowledge evidence. It has not been restarted, modified, or supplied real protected corpus data. Additional blockers are narrow approved RO input, independently controlled writable scratch, an exact authorized image, owner-bound receipts, and independent rollback/partition testing. Keep draft PR #425 unmerged and P0 #414 open until proven.
