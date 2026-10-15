# Offline convergence-evidence readback — research-only (2026-10-15)

**No production deployment, merge or release approval.**

The existing `validate-final-cutover-evidence.py` validates that convergence
evidence path and SHA-256 *fields* are present and formatted, but does not read
or hash the referenced artifact. That is a manifest-shape check, not file
verification. The independent `scripts/validate-convergence-readback.py`
adds offline verification of the nested v2 report proposed by draft #371.

Run only with synthetic/disposable files until consumer schema review:

```sh
python3 scripts/validate-convergence-readback.py \
  deploy/reconciliation/final-cutover-evidence.yaml --root .
```

It never writes to disk or makes a network call. A successful check prints
`CONVERGENCE_READBACK: VERIFIED_ARTIFACT_CONSISTENCY_ONLY`. Failure prints
bounded `BLOCKED` reason codes without untrusted payload or token values.

Checks: relative `artifacts/` path and symlink escape protection, file size
<=1 MiB, raw-byte SHA-256 matched against the manifest, embedded canonical
JSON SHA-256, no duplicate JSON keys, exact outer schema v1/nested view v2,
status PASS and empty failures, valid typed projection identities and bounded
numeric admissions/retirements, unexpired projection, and source/router
generation/revision digest/checksum matching the manifest.

This **does not prove authenticity or authorization**: a party controlling
both the report and manifest could rehash both. The operator must separately
establish source identity, independently governed credentials, resource-side
commit/readback, replay/fencing, and release approval. The current checker is
**not wired into the legacy cutover gate**: schema-owner and consumer review
is required before modifying the release decision.

Research refs: Auto-Assist drafts #368, #369, #370, #371; credential transport
issue #299. No privileged request, graph write, model promotion or device
operation is required for these tests.
