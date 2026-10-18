# AssistX signed DSSE image provenance — verification-only research

October 17, 2026, Fleet Commander on x1-370.
**RESEARCH / DRAFT / production HOLD / NO-GO.**

A standalone, read-only verifier of an externally issued Ed25519 DSSE
envelope holding a narrowly constrained in-toto/SLSA v1 build-provenance
statement. It is NOT a builder, signing service, KMS, key-provisioner,
trust-root authority, or production release gate.

The code supplements AssistX PR408 local Docker image-object identity
observer. It does not retrospectively attest the running images.

## Implementation

The script scripts/verify_signed_image_provenance.py verifies:

- DSSE-v1 PAE content-type/payload domain separation, canonical base64,
  a single Ed25519 signature and separately supplied root-key SHA256.
  An unsigned keyid hint cannot confer trust.
- Strict duplicate-JSON-key-rejecting parsing of a narrowly constrained
  in-toto Statement v1 with SLSA provenance/v1 predicate.
- Signed subject service-specific registry manifest SHA256 matches the
  caller-pinned digest and separately observed local RepoDigest.
  Registry manifest and local Docker image/config IDs are distinct.
- Signed source repo/immutable 40-hex Git SHA in both externalParameters
  and resolved source dependency; externally expected builder ID,
  buildType and exact service/subject. Service repository is restricted.
- Fresh local pinned Unix-Docker socket two-pass runtime scan, stable
  daemon, matching container/image object and running service.
- Bounded absolute symlink-refusing attestation/public-key file reads.
  No private keys or environment secrets are read or logged.

**All results deny release.** A successful result establishes only that
a signature cryptographically verifies under the supplied public key
and its digest matches the local observed manifest. It ALWAYS returns
production_decision=HOLD, promotion_authorized=false,
externally_authorized_builder_root_proven=false,
signed_source_to_runtime_release_authority=false.
Builder custody, signer independence, trust root authority,
transparency, expiry/revocation and runtime execution authority
remain unverified.

## Executed Fleet Commander evidence

- 37 focused offline synthetic DSSE/SLSA tests PASS. In-process,
  ephemeral Ed25519 signing keys exist ONLY inside the synthetic tests,
  not as provisioned production signer resources.
- Existing companion image custody source suite 31 PASS.
  Combined 68 PASS, Ruff and Python compilation PASS.
- Rejected: signature tamper, key swap, signed wrong Git commit/source,
  wrong builder/buildType, wrong subject/service/dependency,
  wrong runtime manifest/container, duplicate signed JSON keys,
  noncanonical base64, altered DSSE payload type, spoofed keyid,
  extra fields, symlink public key, oversized envelope and unsafe errors.
- Full CLI synthetic fixture: valid ephemeral signer and payload plus
  synthetic runtime produced signature_valid_under_supplied_key=true,
  but HOLD. Different supplied key was denied.
- Actual production READ-ONLY negative: the CLI checked the live
  assistx-api Docker identity via the pinned local scan, then an
  intentionally absent attestation file caused exit code 3:
  production_decision=HOLD, promotion_authorized=false. No
  independently trusted builder/root was available for live images.
- Production service startup/image IDs were unchanged; existing
  operational Auto-Assist main checkout was not modified.

## Remaining release-blocking gates

1. Independently governed builder signer, trusted verifier root,
   source input verification and signed external builder attestation.
   A synthetic public key or retroactive self-sign of an old image
   is NOT valid independent provenance.
2. Controlled registry build/signing, independent revocation and
   transparency policy, manifest-to-node-local image proof and
   attested process launch.
3. Production Neo4j API/worker/Hermes shared admin identity,
   dedicated Context DBMS/least-privilege migration and
   unauthorized direct Bolt denial (Context #21/#31).
4. Resource-owned NON-restorable effect-time COMMIT root and native
   privileged database-owner bypass denial: Xwing software journal
   and Lenovo DB co-restore still replays signed effects (#46).
5. Context exact-head hosted CI zero executed runner steps (#45);
   owner-bound supervisor/failover and rollback not accepted.
6. New PR exact-head Auto-Assist hosted CI must run and pass,
   separately from parent #408 hosted successes.

## Smallest safe next experiment

After explicit operator review, create a NEW disposable build,
not a relabeled historical production image. Pin a source/tree SHA
and hermetic build, independently sign a DSSE SLSA statement under
a separately governed key, and verify the manifest and a strictly
isolated disposable running image from another verifier identity.
Negative cases: builder compromise, key revocation, replaced image,
stale source and rollback. Do not treat synthetic signatures as
release attestations.

**No production merge, build, push, Docker restart, image replacement,
credential rotation, Neo4j change, network change, TPM or failover.**
