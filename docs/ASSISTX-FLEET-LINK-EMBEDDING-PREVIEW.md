# AssistX: observation-only Fleet Model Link embedding admission preview

**October 10, 2026 — draft only.** Stacked on [AssistX operator chat canary #285](https://github.com/scottjoyner/auto-assist/pull/285) and the [Fleet Model Link embedding route #31](https://github.com/scottjoyner/fleet-hardware/pull/31). This is **not** a client embedding API, production approval, loaded-state attestation, or a way for an agent to generate embeddings.

## Contract

`GET /api/fleet-link/embeddings/preview` reuses AssistX's **existing operator authentication**. It is separately disabled unless `ASSISTX_FLEET_LINK_EMBEDDING_PREVIEW=1` is explicitly set. Enabling it does **not** enable the independent `ASSISTX_FLEET_LINK_CANARY` chat POST route; it creates no new inference route and cannot promote models, authorize dispatch, autoload models, or write graph data.

It uses only `ASSISTX_FLEET_LINK_URL` with a literal, unambiguous `http://127.0.0.1:<high-port>` and `ASSISTX_FLEET_LINK_RUNTIME_ID`. The namespace is that of the AssistX server process (not assumed to be the host). HTTPX follows no redirects or system proxies; connection/read timeouts and JSON body sizes are bounded. If the sidecar URL is malformed, feature disabled, there is no fresh approved embedding artifact, or the runtime authority is unavailable, **no network calls are made** and the result is a sanitized `NOT_VERIFIED` with zero counts.

### Approval intersection (three distinct authorities)

1. **AssistX runtime authority:** The existing internal canonical Ed25519 projection issuer (`_current_runtime_projection`) must issue fresh, version-2 `source=assistx` evidence with generation, signature format, fresh timestamps, and a recomputed matching projection checksum. The preview consumes the issuer's *in-process result*, not an untrusted externally supplied signed blob. As a separate diagnostic, the preview does **not** independently verify the signature with a pinned external public key.
2. **Exact admission:** An enabled `openai_compatible` runtime is matched by **pinned runtime ID** and *one exact approved loopback AccessPath*, and a model with `sha256:` followed by 64 lowercase hex digits has a single exact `node/alias` `provider_model`. The model's capabilities must contain both `local_only` and `embedding` or `embeddings`, and must **not** contain `chat` or have agent/code execution enabled. `auto/*` magic routes and duplicate model IDs are quarantined.
3. **Sidecar readiness observation:** Only `GET /fleet/nodes` and `GET /fleet/embeddings` are issued. The model must be `kind=embedding`, `ready_now=true` and `status=READY_PROVED_RECENTLY` in the unexpired status document, and simultaneously listed as an embedding in the embedding inventory. Duplicate rows, expired catalogue, or any missing/malformed evidence fail closed. Inventory presence alone never upgrades readiness.

The preview returns a small aggregate `schema=assistx-fleet-link-embedding-preview.v1` with `mode`, `admitted_embedding_count`, `observed_ready_embedding_count`, `matched_embedding_count`, an optional coarse catalog generation hint, and explicit false values for `execution_authorized`, `automatic_promotion`, `artifact_identity_independently_proved`, and `sidecar_identity_cryptographically_verified`. It returns **no** per-node addresses, aliases, opaque mobile handles, fingerprints, signatures, public keys, vector contents, or diagnostic exception messages.

### Why a positive intersection is insufficient to route users

The sidecar's `/fleet/nodes` JSON is a status view and **not itself cryptographically signed** for this HTTP caller; nor does it presently report an independently verified SHA-256 digest for the exact loaded weights. The signed Fleet Link catalog binds a public `node/alias` to an `upstream_id` and local port, while the separately admitted AssistX projection binds that route to an artifact hash. This preview observes their *route-name intersection*, **not an independent assertion that the running model bytes match the approved fingerprint**. No `matched_embedding_count` by itself is authority to generate embeddings or inject retrieved NAS documents into a prompt.

Physical Lenovo `127.0.0.1:1237` produced a validated 768-dimensional embedding through an **ephemeral** Fleet Link gateway, as recorded in PR #31, but no permanently approved catalog, real physical mTLS peer identity, matching AssistX graph admission, or iPhone permissions have been witnessed for the service. This preview does not change those gates.

## Verification

```bash
cd /home/scott/git/wt-assistx-fleet-link-embedding-preview-20261010
PYTHONPATH=src python3 -m pytest -q \
  tests/test_fleet_link_embedding_preview.py \
  tests/test_fleet_link_canary.py tests/test_mobile_runtime_catalog.py \
  tests/test_mobile_agent_routes.py tests/test_runtime_projection.py \
  tests/test_runtime_projection_v2.py tests/test_router_integration.py

PYTHONPATH=src python3 scripts/fleet_link_embedding_joint_preview.py \
  --fleet-link-root /home/scott/git/wt-fleet-link-embeddings-20261010
```

The joint test runs Fleet Model Link's actual HTTP handler on an ephemeral loopback port, a disposable localhost embedding model providing **GET only**, two separate ephemeral Ed25519 signing keys, and an isolated AssistX FastAPI operator route. It proves one synthetic approved-model/readiness intersection with **zero embedding POSTs**. It never connects to a live model, executes a generated vector, changes a listener, or writes to NAS. Unit tests cover auth, separate feature toggles, invalid URLs and signatures/checksums, expired approvals, chat/embedding confusion, missing permissions, duplicate model rows, network failures and no-POST behavior.

### Compatibility fix

The existing `mobile_agent_routes._current_runtime_projection` imports `build_runtime_projection_v2`, whereas the signing module exposes the identical canonical implementation under `build_runtime_projection`. This branch exports a backward-compatible alias to the **same** Ed25519 issuer so the live signed mobile catalog path can resolve the existing import. It does not enable unsigned fallback or bypass the existing approvals.

## Deployment holds

- Independently pin the actual sidecar process, signed catalog signer and expiry, network namespace, filesystem custody and raw loopback model isolation. Do not infer container localhost from host localhost.
- Prove the physical model's artifact SHA-256, admitted loaded state and approved capacity/access-path evidence without inventing graph approvals; add a binding witness before calling any model *authorized*.
- Run supervised physical inter-node mTLS verification before cross-node routing; keep unrelated wildcard chat listeners and NAS transfer workloads untouched.
- Restrict mobile exposure to Kipnerter's existing authenticated scopes; embedding-based search and context injection must have independent permission and trace lineage.
