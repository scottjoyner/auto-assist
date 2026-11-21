# Strict signed-gateway trusted-header provenance — 2026-10-22

**Release decision: HOLD / NO-GO.** Draft PR #425 and P0 #414 remain open. No production AssistX config, API process, Hermes image, protected storage, real user credentials or live task records were modified.

## Exact source issue

The existing `assistx.api::_auth_user_from_credentials` previously returned any nonempty `TRUSTED_AUTH_HEADER` value ahead of Basic authentication. This can be legitimate when a trusted, authenticated gateway strictly strips and reissues that header, but it **must not** be treated as independently trustworthy when a client could reach that handler directly. A live Fleet Commander *read-only* Docker preflight found this legacy mode configured on production AssistX, with the container's published port 8000 bound on 127.0.0.1. An additional listener on the host's Tailscale address/port 8000 was observed but its ownership/forwarding chain was NOT verified. There is no claim of a proven remotely reachable bypass.

## New opt-in fail-closed behavior

When **both** `ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY=true` and `ASSISTX_REQUIRE_OWNER_BINDING=true` are configured, the legacy trusted-user header is not sufficient to authenticate. For `POST /api/tasks`, a trusted-header principal must exactly match the already-established canonical `scope.state.verified_owner`, which the separate signed-gateway middleware sets only after verifying a pinned Ed25519 attestation bound to the request method, path, body hash, freshness and one-time nonce. Missing, altered, mismatched or noncanonical owner credentials deny the header path, with no downgrade to Basic even if valid Basic credentials accompany an invalid trusted header. For other request paths, **header-only authentication is denied in strict signed mode** pending a separately reviewed authentication policy. When no trusted header is supplied, the existing Basic-auth path remains available. When the signed-gateway flags are disabled, legacy application behavior remains unchanged, including current production.

This does not itself establish identity at the original gateway: the external signer must first authenticate users, and its private key custody/rotation and all proxy ingress stripping still require independent validation. Trusting an arbitrary incoming `scope.state.verified_owner` without the signed middleware would be unsafe; the code relies on the gated ASGI installation contract and is not a complete generic proxy-identity system.

## Synthetic tests and production gating

`tests/test_signed_gateway_trusted_header_hardening.py` compiles the **exact** checked-in `_auth_user_from_credentials` function from `src/assistx/api.py` without importing the full production API dependency graph, then checks forged bare headers, mismatched verified owners, incorrect paths and methods, downgrade-with-Basic refusal, Basic fallback without a header, and unchanged legacy behavior. A complete isolated FastAPI + `SignedGatewayOwnerMiddleware` request pair verifies a correctly signed owner/header is accepted, but a signed A identity with B's header, an unsigned header, or a header-only GET is denied. All identities, credentials, keys and the private replay database are disposable synthetic test inputs.

**Important lint note:** The broader `src/assistx/api.py` currently has unrelated existing whole-file Ruff findings; this slice does not mass-reformat unrelated production code. The new test module passes its requested lint and compilation; hosted CI is the authoritative check for release status.

## Unresolved NO-GO

The deployed AssistX API still has signed-gateway owner binding disabled. The source-only guard does not fix already-running legacy header trust. Needed next: independently authenticated signing gateway and verified ingress header stripping, key rotation and independent custodial authority, rollback-resistant nonce/high-water witness, live two-human claim/heartbeat/receipt/revocation proof, approved 16-mount safe storage cutover, immutable signed release and rollback. **No merge or production deployment is approved by this change.**
