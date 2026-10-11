# Independently pinned AssistX projection signature witness

**Experimental read-only extension, October 10, 2026.** Stacked on AssistX draft PR #286 and Fleet Model Link embedding PR #31. No model execution or deployment changes.

The existing `GET /api/fleet-link/embeddings/preview` already compared a fresh, internally issued AssistX projection against the Fleet Model Link readiness and embedding inventory. The original check validated projection timestamps, source, schema, checksum and signature *format*, but **did not verify the Ed25519 signature bytes using an independent public key**. The projection still comes from AssistX's trusted in-process issuer; structural validity must not be described as a separately verified cryptographic signature.

This change optionally reads `ASSISTX_RUNTIME_PROJECTION_VERIFY_PUBLIC_KEY_HEX` (32-byte Ed25519 public key, lowercase 64-character hex). It validates the current issuance signature using the same canonical `signing_message` contract and independently recomputes the projection checksum. It never opens the private key, sends the public key to a client, or changes any model/backend configuration.

The operator-only preview now includes `projection_ed25519_pin_verified`:

- **true:** Valid Ed25519 signature on the currently checked projection matched a locally configured pinned public key and an independently checked checksum. This authenticates only the projection bytes to that key.
- **false with no public-key pin:** Existing observation-only route counts remain available, but the signature has **not** been independently authenticated. This state can never authorize model execution.
- **false with an invalid/mismatched configured pin:** The preview returns `NOT_VERIFIED`, zero counts, and makes **no** calls to Fleet Model Link. Even a correctly shaped signature cannot bypass this explicit misconfiguration/failure.

This additional witness cannot prove that the signing key is under the correct human or organizational custody, that the approved projection reflects the present Neo4j facts, that the sidecar's process matches the expected commit, or that the actual running model bytes match an independently approved SHA-256 fingerprint. The response continues to set `execution_authorized=false`, `automatic_promotion=false`, `artifact_identity_independently_proved=false`, and `sidecar_identity_cryptographically_verified=false`. No mobile or embedding execution endpoint is created.

## Local proof and deployment holds

The pinned key must come from an independently approved, access-controlled signer distribution workflow. **Do not manufacture a key from the production private key in an agent session or promote a synthetic test key.** The operator must verify its public-key provenance before setting the environment variable on the live service. Configuration changes on production AssistX were not performed.

A read-only Lenovo check observed that the process listening only on `127.0.0.1:1237` maps a file on local ext4 with the same inode and device as the named embedding GGUF (146,146,432 bytes). This is *metadata custody evidence only*: it is neither a previously approved model SHA-256 nor a cryptographically attested execution receipt. No large-file hash, NAS scan, service restart or power change was performed. Even a verified AssistX projection signature cannot fill that separate artifact gap.

## Tests

```bash
cd /home/scott/git/wt-assistx-fleet-link-signature-witness-20261010
PYTHONPATH=src python3 -m pytest -q tests/test_fleet_link_signature_witness.py tests/test_fleet_link_embedding_preview.py
PYTHONPATH=src python3 -m pytest -q tests/test_fleet_link_signature_witness.py tests/test_fleet_link_embedding_preview.py tests/test_fleet_link_canary.py tests/test_mobile_runtime_catalog.py tests/test_mobile_agent_routes.py tests/test_runtime_projection.py tests/test_runtime_projection_v2.py tests/test_router_integration.py
PYTHONPATH=src python3 scripts/fleet_link_embedding_joint_preview.py --fleet-link-root /home/scott/git/wt-fleet-link-embeddings-20261010
```

Tests use ephemeral Ed25519 signers and synthetic projections; none authenticates a real production graph record. The cross-repository integration uses actual code with only disposable loopback nodes and performs zero inference POST requests.
