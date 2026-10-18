# AssistX remote validate-before-sign research

Date: October 17, 2026. Execution: Fleet Commander on x1-370 and destroyer.
Status: **DRAFT — 114 focused tests PASS, two-node disposable physical test PASS; production HOLD / NO-GO.**

## Incremental implementation

This is a stacked change after Auto-Assist draft PR410, exact head
109058ab6c2d8e7b71bfb7146edc5acbe26670db.

The previous disposable lab sent an x1-written in-toto/SLSA-shaped
statement directly to Destroyer for signing without checking the
underlying image. The new research runner sends the **real OCI TAR**
instead. Destroyer separately inspects the TAR, recomputes the
manifest, configuration and compressed/uncompressed layer digests,
verifies the exact published Git source blob, independently forms
the in-toto statement, and only then creates a **new ephemeral test**
Ed25519 key in process memory for DSSE signature.

New sources:
- scripts/disposable_remote_artifact_attestor.py
- tests/test_disposable_remote_artifact_attestor.py

The research lab script run_disposable_signed_build_lab.py now pins
the remote verifier file to native Git blob
23891d62a04347aca19070f35fabba761e8476f2
before any SSH, forwards raw OCI bytes rather than an unverified
statement, compares the remote manifest/source digest with its own
independently computed data and verifies the signed subject against
the exact isolated running Docker image. It uses BatchMode and
StrictHostKeyChecking. A mismatched or foreign signer program,
unrecognized signer response or conflicting digest aborts the lab.

The remote program is a bounded OCI archive reader (8 MiB max,
regular file member allowlist, max count, SHA256 descriptors,
bounded gzip decompression, rootfs uncompressed diff-IDs, nonroot
entrypoint and EXACT embedded source Git blob). No archive member
is extracted to a host filesystem. Key material is never persisted,
returned or included in metadata; only test public key and DSSE
envelope are returned after successful verification.

## Actual tests and physical evidence

- 114 focused tests passed: 101 inherited from PR410 plus
  13 additional remote verifier/attacker controls.
- Hosted-style pytest with PYTHONPATH unset passed all 114.
- Ruff and Python compilation passed.
- x1-370 compiled/loaded and ran a FROM-scratch two-layer OCI image
  with Docker buildx --network none, no pulls, no registry push.
  OCI manifest: ed073ed557145da73a3847d8d61ada786d77f23c777aa67e581aaf664a5c60b6.
- Destroyer independently verified OCI archive/source and signed
  its own statement. x1 verified signature, exact source/manifest,
  and running disposable network-none/read-only/nonroot/cap-drop/no
  mounts Docker container. Mutated signed statement and substituted
  running image manifest were refused.
- **Physical attacker negative on Destroyer passed:** a re-packed
  OCI archive with altered source but internally consistent SHA256
  descriptors was rejected BEFORE SIGNING, without returning any
  public key or signed statement.
- Disposables were deleted with exact identity matching. Production
  Neo4j, AssistX API, worker, Hermes, Auto-Router unchanged.

## Trust limitations — production remains NO-GO

The remote attestor code and expected Git hash are STILL distributed
by x1-370 over SSH, not installed from a separately approved root.
The test signing key is created on demand, not operator-controlled.
The in-toto/SLSA-shaped builder identity is a synthetic claim, not an
independently audited reproducible build of the complete application
source and dependencies. There is no registry transparency, signature
revocation, source-tree material attestation, production signer
separation, or non-restorable execution authority. This work closes
a disposable blind-signing hole but NOT P0 production provenance.

Every success still reports production_decision=HOLD and
promotion_authorized=false. No merge, deployment, image release,
credential rotation, graph or SQL mutation, model autoload, or
fleet failover was performed.

Other independent P0s remain: production Neo4j API/worker/Hermes
share admin principal (#21/#31), Context #46 grant/SQLite
co-restoration replays an earlier signed effect and has native
DB-owner bypass, Context #45 hosted Actions fails before runner,
and owner-bound supervision, revocation and rollback are unproven.

Next independent test: separate operator-approved signer trust root,
complete reproducible application/checkout materials, actual
source-verifying builder, signed registry manifest/revocation
controls and another identity performing final runtime verification,
all using disposable assets before any production admission.
