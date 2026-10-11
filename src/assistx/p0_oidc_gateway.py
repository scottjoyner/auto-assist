"""P0 #37: default-OFF identity-bound local-model gateway experiment.

This code NEVER trusts caller-supplied user, subject, tenant, traceparent or
TRUSTED_AUTH_HEADER for ownership. It verifies pinned IdP RS256 JWTs and maps
the signed issuer+sub through a server-owned human allowlist, then creates an
Ed25519 delegation for the existing Auto-router verifier.

No client receives the router service credential or gateway signing key.
Not a real IdP acceptance claim, trace-custody service or provider attestation.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}\Z")
_ROUTER_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_JOSE = re.compile(r"[A-Za-z0-9_-]+\Z")


class HumanGatewayDenied(ValueError):
    """Sanitized errors only. Never echo JWTs, passwords or signing keys."""


def _read_private(path: str, *, max_bytes: int = 32768) -> bytes:
    if not isinstance(path, str) or not path.startswith("/"):
        raise HumanGatewayDenied("p0 gateway requires pinned absolute file paths")
    p = Path(path)
    try:
        meta = p.lstat()
        if (not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077
                or meta.st_uid != os.getuid() or meta.st_size > max_bytes):
            raise HumanGatewayDenied("p0 gateway private file permissions invalid")
        return p.read_bytes()
    except OSError as exc:
        raise HumanGatewayDenied("p0 gateway private file unavailable") from exc


def _strict(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise HumanGatewayDenied("duplicate signed identity claim")
        result[key] = value
    return result


def _json(data: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(data, object_pairs_hook=_strict)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HumanGatewayDenied("invalid signed identity envelope") from exc
    if not isinstance(payload, dict):
        raise HumanGatewayDenied("signed identity must be an object")
    return payload


def _part(raw: str) -> bytes:
    if not isinstance(raw, str) or not _JOSE.fullmatch(raw):
        raise HumanGatewayDenied("invalid signed identity encoding")
    try:
        data = base64.urlsafe_b64decode(raw + "=" * ((-len(raw)) % 4))
    except (ValueError, TypeError, binascii.Error) as exc:
        raise HumanGatewayDenied("invalid signed identity encoding") from exc
    if base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii") != raw:
        raise HumanGatewayDenied("noncanonical signed identity")
    return data


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _signed_jwt(header: dict[str, Any], claims: dict[str, Any], key) -> str:
    unsigned = ".".join([
        _b64(json.dumps(header, sort_keys=True, separators=(",", ":")).encode()),
        _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()),
    ])
    return unsigned + "." + _b64(key.sign(unsigned.encode("ascii")))


class VerifiedHumanGateway:
    """Pinned identity-verification and model-scoped delegation issuer."""

    def __init__(
        self, *, jwks: dict, roster: dict, gateway_key: Ed25519PrivateKey,
        gateway_kid: str, router_service_bearer: str, router_url: str,
        audience: str,
    ):
        if not _ID.fullmatch(gateway_kid) or not audience:
            raise HumanGatewayDenied("invalid gateway signing key or audience")
        if not router_url.startswith("https://") or not router_url.endswith(
            "/v1/chat/completions"
        ):
            raise HumanGatewayDenied("production router URL must be pinned HTTPS")
        if not router_service_bearer or len(router_service_bearer) < 20:
            raise HumanGatewayDenied("gateway service credential unavailable")
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise HumanGatewayDenied("invalid pinned IdP key set")
        if not isinstance(roster, dict) or not isinstance(roster.get("users"), list):
            raise HumanGatewayDenied("invalid human identity roster")
        self.audience = audience
        self.issuer = str(roster.get("issuer") or "")
        if not self.issuer.startswith("https://"):
            raise HumanGatewayDenied("IdP issuer must be pinned HTTPS")
        self.oidc_keys: dict[str, Any] = {}
        for entry in jwks["keys"]:
            if not isinstance(entry, dict) or entry.get("kty") != "RSA":
                raise HumanGatewayDenied("unsupported IdP signing key")
            kid = entry.get("kid")
            if not isinstance(kid, str) or not _ID.fullmatch(kid) or kid in self.oidc_keys:
                raise HumanGatewayDenied("invalid or duplicate IdP key ID")
            if entry.get("alg") not in (None, "RS256"):
                raise HumanGatewayDenied("unapproved IdP signature algorithm")
            try:
                n = int.from_bytes(_part(entry["n"]), "big")
                e = int.from_bytes(_part(entry["e"]), "big")
                pub = rsa.RSAPublicNumbers(e, n).public_key()
            except (KeyError, ValueError, TypeError) as exc:
                raise HumanGatewayDenied("invalid pinned IdP RSA key") from exc
            if pub.key_size < 2048:
                raise HumanGatewayDenied("weak IdP public key")
            self.oidc_keys[kid] = pub
        if not self.oidc_keys:
            raise HumanGatewayDenied("IdP key set cannot be empty")
        self.humans = {}
        seen_human_owners: set[tuple[str, str]] = set()
        for row in roster["users"]:
            if not isinstance(row, dict) or set(row) != {
                "sub", "tenant", "subject", "models", "kind"
            } or row["kind"] != "human":
                raise HumanGatewayDenied("unapproved human identity roster entry")
            sub, tenant, subject = row["sub"], row["tenant"], row["subject"]
            models = row["models"]
            if (
                not all(isinstance(x, str) and _ID.fullmatch(x)
                        for x in (sub, tenant, subject))
                or not isinstance(models, list) or not models
                or not all(isinstance(m, str) and _ROUTER_MODEL.fullmatch(m)
                           for m in models)
                or sub in self.humans
            ):
                raise HumanGatewayDenied("invalid or duplicate human mapping")
            # Distinct authenticated IdP subjects must not silently collapse
            # into the same human owner. Account linking requires a separate,
            # audited identity migration and cannot happen via this roster.
            owner = (tenant, subject)
            if owner in seen_human_owners:
                raise HumanGatewayDenied("ambiguous human identity mapping")
            seen_human_owners.add(owner)
            self.humans[sub] = (tenant, subject, frozenset(models))
        self.gateway_key = gateway_key
        self.gateway_kid = gateway_kid
        self.router_service_bearer = router_service_bearer
        self.router_url = router_url

    def authenticated_human(self, idp_jwt: str, *, now: int | None = None):
        if not isinstance(idp_jwt, str) or not 1 <= len(idp_jwt) <= 8192:
            raise HumanGatewayDenied("invalid IdP token length")
        parts = idp_jwt.split(".")
        if len(parts) != 3:
            raise HumanGatewayDenied("invalid IdP token segments")
        header, claims = _json(_part(parts[0])), _json(_part(parts[1]))
        kid = header.get("kid")
        if (
            header.get("alg") != "RS256" or header.get("typ") != "JWT"
            or set(header) != {"alg", "typ", "kid"}
            or kid not in self.oidc_keys
        ):
            raise HumanGatewayDenied("untrusted IdP signing header")
        try:
            self.oidc_keys[kid].verify(
                _part(parts[2]), f"{parts[0]}.{parts[1]}".encode("ascii"),
                padding.PKCS1v15(), hashes.SHA256(),
            )
        except Exception as exc:
            raise HumanGatewayDenied("IdP signature invalid") from exc
        current = int(time.time()) if now is None else now
        sub = claims.get("sub")
        issued, not_before, expires = (
            claims.get("iat"), claims.get("nbf"), claims.get("exp")
        )
        if (
            claims.get("iss") != self.issuer
            or claims.get("aud") != self.audience
            or not isinstance(sub, str) or sub not in self.humans
            or any(type(x) is not int for x in (issued, not_before, expires))
            or issued > current + 5 or not_before > current
            or not (issued <= current < expires)
            or not 0 < expires - issued <= 3600
        ):
            raise HumanGatewayDenied("IdP subject, audience or session invalid")
        return self.humans[sub]

    def issue(self, jwt: str, model: str, *, now: int | None = None):
        tenant, subject, allowed = self.authenticated_human(jwt, now=now)
        if not isinstance(model, str) or model not in allowed:
            raise HumanGatewayDenied("model not authorized for verified subject")
        current = int(time.time()) if now is None else now
        root, attempt = str(uuid.uuid4()), str(uuid.uuid4())
        claims = {
            "schema": "p0-user-shadow-v1",
            "iss": "trusted-user-gateway-p0",
            "aud": "auto-router-p0-shadow-user",
            "tenant": tenant,
            "subject": subject,
            "root_request_id": root,
            "attempt_id": attempt,
            "model_handle": model,
            "iat": current,
            "nbf": current,
            "exp": current + 60,
            "jti": str(uuid.uuid4()),
        }
        token = _signed_jwt({
            "alg": "EdDSA", "typ": "auto-router-user-delegation+jwt",
            "kid": self.gateway_kid,
        }, claims, self.gateway_key)
        return token, claims

    async def forward(self, jwt: str, payload: dict[str, Any], *,
                      transport=None):
        """One-shot nonstreaming canary. No fallback, redirects or model autoload."""
        if not isinstance(payload, dict) or set(payload) != {
            "model", "messages", "max_tokens"
        }:
            raise HumanGatewayDenied("unsupported gateway body")
        messages = payload["messages"]
        limit = payload["max_tokens"]
        if (
            not isinstance(messages, list) or not 1 <= len(messages) <= 16
            or type(limit) is not int or not 1 <= limit <= 64
            or not all(isinstance(m, dict) and set(m) == {"role", "content"}
                       and m.get("role") in {"user", "assistant", "system"}
                       and isinstance(m.get("content"), str)
                       and len(m["content"]) <= 4096 for m in messages)
        ):
            raise HumanGatewayDenied("invalid bounded gateway prompt")
        delegation, claims = self.issue(jwt, payload["model"])
        headers = {
            "Authorization": "Bearer " + self.router_service_bearer,
            "X-Auto-Router-User-Delegation": delegation,
            "Idempotency-Key": claims["attempt_id"],
        }
        # Only generated headers and validated JSON are passed to the router.
        # Caller-supplied user, subject, metadata and trusted-user headers
        # are not forwarded under any circumstances.
        try:
            async with httpx.AsyncClient(
                transport=transport, timeout=10.0, follow_redirects=False,
                trust_env=False,
            ) as client:
                # Streaming ingestion with a hard ceiling matters even for a
                # non-streaming completion: client.post() buffers unbounded
                # provider bytes *before* a later len(response.content) check.
                async with client.stream(
                    "POST", self.router_url, json=payload, headers=headers,
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise HumanGatewayDenied("unexpected router redirect")
                    length = response.headers.get("content-length")
                    if length is not None:
                        try:
                            declared = int(length)
                        except ValueError as exc:
                            raise HumanGatewayDenied("invalid router response length") from exc
                        if declared < 0 or declared > 262144:
                            raise HumanGatewayDenied("oversized router response")
                    received = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(received) + len(chunk) > 262144:
                            raise HumanGatewayDenied("oversized router response")
                        received.extend(chunk)
                    code = response.status_code
        except httpx.HTTPError as exc:
            raise HumanGatewayDenied("verified router unavailable") from exc
        data = _json(bytes(received))
        return code, data


def install_verified_human_gateway(app: FastAPI) -> None:
    """No new endpoint or route at all unless explicitly configured."""
    enabled = os.getenv("ASSISTX_P0_VERIFIED_HUMAN_GATEWAY_ENABLED", "").lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return
    try:
        jwks = _json(_read_private(os.getenv("ASSISTX_P0_IDP_JWKS_FILE", "")))
        roster = _json(_read_private(os.getenv("ASSISTX_P0_HUMAN_ROSTER_FILE", "")))
        key = serialization.load_pem_private_key(
            _read_private(os.getenv("ASSISTX_P0_ROUTER_SIGNING_KEY_FILE", "")),
            password=None,
        )
        if not isinstance(key, Ed25519PrivateKey):
            raise HumanGatewayDenied("router delegation key must be Ed25519")
        service = _read_private(
            os.getenv("ASSISTX_P0_ROUTER_SERVICE_BEARER_FILE", ""),
            max_bytes=4096,
        ).decode("ascii").strip()
        issuer = VerifiedHumanGateway(
            jwks=jwks, roster=roster, gateway_key=key,
            gateway_kid=os.getenv("ASSISTX_P0_ROUTER_SIGNING_KID", ""),
            router_service_bearer=service,
            router_url=os.getenv("ASSISTX_P0_ROUTER_URL", ""),
            audience=os.getenv("ASSISTX_P0_IDP_AUDIENCE", ""),
        )
    except (HumanGatewayDenied, ValueError, TypeError, UnicodeDecodeError) as exc:
        raise RuntimeError("P0 verified gateway refused unsafe startup") from exc
    router = APIRouter()

    @router.post("/api/p0/verified-local-chat")
    async def forwarded_verified_chat(request: Request):
        # Avoid trusting the legacy HTTP Basic single-user or unverified
        # TRUSTED_AUTH_HEADER paths for this *independent* authentication lane.
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or len(auth) > 8200:
            return JSONResponse(status_code=401, content={"detail": "IdP bearer required"})
        total = 0
        chunks = []
        async for piece in request.stream():
            total += len(piece)
            if total > 65536:
                return JSONResponse(status_code=413, content={"detail": "request too large"})
            chunks.append(piece)
        try:
            body = _json(b"".join(chunks))
            code, result = await issuer.forward(auth[7:], body)
        except HumanGatewayDenied:
            return JSONResponse(status_code=401, content={"detail": "unverified request"})
        # Restrict pass-through to semantic response only, never downstream
        # headers/cookies or the service bearer.
        return JSONResponse(status_code=code, content=result)

    app.include_router(router)
