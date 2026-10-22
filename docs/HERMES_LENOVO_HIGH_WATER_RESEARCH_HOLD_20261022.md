# Hermes high-water — disposable x1-370 / Lenovo witness test

Date: October 22, 2026. **Research-only, production HOLD.** Related to draft auto-assist PR #425 and P0 issue #414.

## Physical evidence

Fleet Commander ran a temporary client on x1-370 and an ephemeral Ed25519 witness signer on Lenovo using the existing noninteractive strict-host-key SSH configuration. Lenovo executed an in-memory Python process for each request: no persistent witness file, key, service, database, mount, or deployment was created there.

The x1-370 side generated a signed synthetic corpus and image-style signed checkpoint in a disposable private temporary directory. A locally owned UNIX socket relay forwarded the worker's fresh challenge across an SSH session to the Lenovo signer, then returned Lenovo's signed answer. Only the remote signer public key was stored temporarily for client verification.

Two separate tests were executed:
- Generation 2 checkpoint with Lenovo's synthetic high-water 2: **ACCEPTED**.
- Generation 1 fully signed checkpoint with Lenovo's synthetic high-water 2: **DENIED**.

Both remote signer processes exited normally, x1-370 scratch and socket were cleaned up, no provider/model/real task was invoked, and production Hermes remained running on its original exact image. This is a genuine **host separation** check of the nonce-bound signing and transport protocol.

Command, manually opt-in on x1-370 only:

    PYTHONPATH=src python3 tests/hermes_high_water_offhost_canary.py --physical-disposable-lenovo

Without the flag it returns HOLD and does not SSH or start processes. The companion 12 offline tests check the opt-in default, SSH trust policy, temporary process/key lifecycle, source path exclusions, strict production image identity, and framing/timeouts:

    PYTHONPATH=src python3 -m pytest -q tests/test_hermes_high_water_offhost_canary.py

## Limits — what this test cannot authorize

This does **not** establish separately controlled administrative custody. Both machines were accessed using the same fleet operator identity, and each synthetic Lenovo signer kept an in-memory constant high-water value, not a durable monotonic ledger. A common administrator could reset both endpoints or alter their code and keys. The local socket relay is a test transport, not a security boundary.

This also does not qualify real protected corpus ingestion, multi-user ownership, concurrent grant fencing, revocation behavior under network partition, a reviewed secure transport relay, or image/credential rotation. The real candidate Hermes container keeps external high-water mode disabled until an actual independent authority is provisioned and authorized.

For a production no-go/approval review, require independent signer and ledger administration, owner-bound append-only generation history with protected revocations, a separately reviewed relay, a multi-host full image+corpus+checkpoint rollback test, signed build provenance, narrow RO input and isolated RW scratch mounts, and independently tested rollback/fencing. P0 #414 remains OPEN and PR #425 DRAFT.

**Decision: HOLD. Do not treat this research signer as a production witness or restart Hermes.**
