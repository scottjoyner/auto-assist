# AssistX: two-node source to OCI to DSSE to isolated process

October 17, 2026. Fleet Commander, x1-370 and destroyer.
**101 focused tests PASS; real two-node disposable build/sign/run/
verification PASS; production HOLD / NO-GO.**

## Scope and evidence tiers

Extends AssistX research draft PR409. This is an operator-UNTRUSTED
fixture, not a production SLSA or independently trustworthy signing
authority. x1-370 builds and runs the isolated image; destroyer signs
the synthetic statement using a new, in-memory ephemeral test key
which is never provisioned for any production service.

New source:
- scripts/verify_disposable_oci_layout.py
- scripts/run_disposable_signed_build_lab.py
- tests/test_verify_disposable_oci_layout.py
- tests/test_run_disposable_signed_build_lab.py

The OCI verifier reads the archive entirely in memory without
extracting archive members to disk. It rejects nonregular/symlink
input, unknown paths, duplicate members, extra blobs, excessive
length/member count, bad index and descriptor, invalid config, wrong
manifest/layer SHA256 and decompressed SHA256, and altered source blob.
Gzip decompression is size-bounded, so compressed bombs are rejected.
The OCI config must specify a nonroot UID and the approved probe
entrypoint. Actual per-service production Dockerfiles are NOT used.

Physical image build: FROM scratch; minimal static C loop + a copy
of the exact Git-published AssistX PR409 verifier source file.
Source blob SHA1 dad70bbde2f51f595545e6ccb0ff41892d8681be,
published in PR409 at Git commit
edcc142af74695a71ffa3316fb7711ed1bcc9fbb.
The same source blob is actually found and hashed in an OCI layer.

**Important limitation:** the tiny generated C heartbeat and
Dockerfile are not proven to originate from the pinned Git commit,
nor was a reproducible complete application build demonstrated.
The remote signer does NOT independently review builder/source
inputs: it signs the supplied bytes. This is a test of the mechanics,
not a trustworthy full-build provenance statement.

Buildx ran --network none, --pull=false, with no SBOM, provenance
generator or registry. The type=oci artifact was SHA256-verified
before Docker load. Docker load on this daemon reported the OCI
MANIFEST hash as the local image ID; the lab verified both exact
image-object ID and running container .Image match that manifest.
Registry RepoDigests are absent: the verified digest was derived
from the OCI layout, NOT a registry manifest receipt.

The test container used network=none, read-only filesystem, UID
65534, dropped all Linux capabilities, no-new-privileges, no
mounts/host ports, memory bound 64 MiB and PID limit 16.
Readback from the actual Docker API checks all these controls.

Using host-key-checked BatchMode SSH, destroyer signed the exact DSSE
PAE payload via an Ed25519 private key generated only in process
memory. It returned a public key and signature, no private key.
x1-370 independently verified exact subject, source repo/commit,
builder claim, build type and manifest SHA256 against the real
disposable container/image. A mutated signed body and a substituted
container image manifest were both denied.

The signer public key is self-supplied and was never independently
approved by an operator. Signature success DOES NOT establish
independent builder identity, custody, key revocation or release
authority. Every result is production_decision=HOLD,
promotion_authorized=false,
operator_trusted_independent_signer_proven=false.

## Actually executed Fleet Commander validation

- Combined **101 tests PASS**:
  31 parent local Docker image identity;
  37 previous DSSE/in-toto/SLSA source tests;
  21 new OCI archive/decompressed-layer and source negatives;
  12 new image ownership, runtime isolation, incorrect signer and
  foreign-daemon/cleanup unit controls.
- Ruff and Python compilation PASS.
- Real two-node x1-370/destroyer disposable physical run PASS.
- OCI image artifact manifest:
  ed073ed557145da73a3847d8d61ada786d77f23c777aa67e581aaf664a5c60b6
- Two OCI image layers verified, one with exact published source blob.
- Remote ephemeral Ed25519 DSSE signed envelope verified; tampered
  signed statement and substituted running-image digest denied.
- OCI container and local image removed with strict created-identity
  matching. A preexisting image is never treated as owned; failure
  before Docker load cannot delete it.
- Production Neo4j, AssistX API, worker, Hermes and Auto-Router
  image IDs and start timestamps were unchanged.
- Production routes, model inventory, NAS storage, TPM, DBMS roles
  and network access were not modified.

## Release gates still HOLD / NO-GO

1. Operator-approved independent signer custody, trusted root and
   actual source-verifying builder; full controlled checkout/source
   tree + dependencies, build artifact, registry publication and
   signed image-to-process launch attestation. Do NOT retroactively
   self-sign old production images and call them proven.
2. Signature revocation/expiry/transparency, malicious builder,
   poisoned checkout, operator-separated verification, stale source,
   builder split-brain, image-substitution and rollback negatives on
   a full controlled production-like build.
3. Production Neo4j admin role is shared among API/worker/Hermes;
   Context dedicated graph database identity cutover and noncaller
   native Bolt denial remain P0 (#21/#31).
4. Context resource-owned effect-time immutable spend authority
   remains P0: Xwing signed grant-journal and Lenovo native SQLite
   co-restoration can replay signed effects, privileged DB owner
   bypass, ambiguous COMMIT not recovered (#46).
5. Context hosted CI zero executed runner steps (#45); the new
   research PR must obtain its own real hosted exact-head jobs.
6. AssistX/Auto-Router operator-bound receipts, active supervision,
   revocation, split-brain, restricted canary and rollback open.

**No production merge, service restart, image release/push,
persistent signer key provisioning, live DBMS/credential/network
change, TPM NV, NAS transfer or automatic fleet failover performed.**
