"""Optional signed gateway owner-attestation gate for POST /api/tasks.

Research contract, NOT a production gateway. The independent gateway must
authenticate the human principal before signing; no user-submitted owner
header or Basic-auth username alone can supply verified_owner.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from starlette.responses import JSONResponse

from .owner_identity import canonical_identity

HEADER = b"x-assistx-signed-owner"
AUDIENCE = "assistx-owner-request-v1"
FIELDS = frozenset({
    "schema", "aud", "owner_issuer", "owner_subject",
    "method", "path", "body_sha256", "issued_at", "expires_at", "nonce",
})
MAX_ATTESTATION = 2048
MAX_BODY = 512 * 1024


class OwnerGatewayDenied(ValueError):
    pass


def _need(ok: Any, reason: str) -> None:
    if not ok:
        raise OwnerGatewayDenied("signed owner gateway: " + reason)


def _private_file_path(value: str, *, file_name: str) -> Path:
    _need(isinstance(value, str) and bool(value)
          and os.path.isabs(value) and os.path.normpath(value) == value,
          file_name + " must be absolute")
    _need(not value.startswith(("/workspace/", "/research/knowledge/")),
          file_name + " cannot use task-controlled storage")
    p = Path(value)
    cursor = Path("/")
    for name in p.parts[1:]:
        cursor /= name
        _need(not cursor.is_symlink(), file_name + " path contains symlink")
    return p


def _public_key() -> Ed25519PublicKey:
    path = _private_file_path(
        os.getenv("ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE", ""),
        file_name="pinned gateway verification key")
    try:
        info = path.stat()
        _need(stat.S_ISREG(info.st_mode) and not (info.st_mode & 0o022),
              "gateway verification key must not be group/world writable")
        raw = path.read_bytes()
        _need(0 < len(raw) <= 4096, "gateway verification key size")
        key = serialization.load_pem_public_key(raw)
        _need(isinstance(key, Ed25519PublicKey), "gateway key must be Ed25519")
        return key
    except (OSError, ValueError, TypeError) as exc:
        raise OwnerGatewayDenied("pinned gateway verification key unavailable") from exc


def _decoded(part: str, *, max_bytes: int) -> bytes:
    _need(bool(re.fullmatch(r"[A-Za-z0-9_-]+", part))
          and len(part) <= max_bytes * 2, "invalid compact attestation")
    try:
        decoded = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (ValueError, TypeError) as exc:
        raise OwnerGatewayDenied("invalid base64url attestation") from exc
    _need(len(decoded) <= max_bytes
          and base64.urlsafe_b64encode(decoded).decode().rstrip("=") == part,
          "noncanonical attestation encoding")
    return decoded


def verify_attestation(raw: str, *, method: str, path: str,
                       request_body: bytes, now: int | None = None) -> dict[str, Any]:
    _need(type(raw) is str and 10 < len(raw) <= MAX_ATTESTATION,
          "missing or oversized signed attestation")
    parts = raw.split(".")
    _need(len(parts) == 2, "invalid attestation framing")
    payload_raw = _decoded(parts[0], max_bytes=1200)
    signature = _decoded(parts[1], max_bytes=64)
    _need(len(signature) == 64, "invalid signature size")
    try:
        _public_key().verify(signature, payload_raw)
        data = json.loads(payload_raw)
        _need(type(data) is dict and set(data) == FIELDS,
              "invalid attestation fields")
        _need(json.dumps(data, sort_keys=True, separators=(",",":"),
                         ensure_ascii=True).encode() == payload_raw,
              "noncanonical signed JSON")
        _need(type(data["schema"]) is int and data["schema"] == 1
              and data["aud"] == AUDIENCE, "wrong schema or audience")
        canonical_identity(data, origin="signed gateway principal")
        _need(type(method) is str and method == "POST"
              and type(path) is str and path == "/api/tasks"
              and data["method"] == method and data["path"] == path,
              "request method/path substitution")
        _need(type(request_body) is bytes and len(request_body) <= MAX_BODY,
              "invalid request body")
        _need(type(data["body_sha256"]) is str
              and data["body_sha256"] == hashlib.sha256(request_body).hexdigest(),
              "request body does not match signed identity")
        _need(type(data["nonce"]) is str
              and bool(re.fullmatch("[0-9a-f]{64}", data["nonce"])),
              "invalid owner grant nonce")
        issued, expires = data["issued_at"], data["expires_at"]
        clock = int(time.time()) if now is None else now
        _need(type(issued) is int and type(expires) is int
              and type(clock) is int and issued <= clock+5
              and issued >= clock-45 and issued < expires
              and clock < expires <= issued+45,
              "stale or implausible attestation")
        return data
    except (InvalidSignature, UnicodeDecodeError, json.JSONDecodeError,
            ValueError, TypeError, OverflowError) as exc:
        raise OwnerGatewayDenied("invalid or forged gateway attestation") from exc


def _consume_nonce(nonce: str) -> None:
    """SQLite replay protection within one custodial DB. NOT rollback-proof."""
    p = _private_file_path(
        os.getenv("ASSISTX_OWNER_GATEWAY_NONCE_DB", ""),
        file_name="owner gateway nonce store")
    _need(p.parent.is_dir() and p.parent.stat().st_mode & 0o077 == 0,
          "nonce directory must be private")
    if p.exists():
        _need(p.is_file() and not (p.stat().st_mode & 0o077),
              "nonce store must be private regular file")
    try:
        # Create with private permissions BEFORE SQLite opens this DB; never
        # leave newly created nonce files briefly world-readable.
        if not p.exists():
            try:
                fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        with sqlite3.connect(p, timeout=3) as db:
            db.execute("PRAGMA busy_timeout=3000")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS used_owner_nonce "
                       "(nonce TEXT PRIMARY KEY, seen_ts INTEGER NOT NULL)")
            db.execute("INSERT INTO used_owner_nonce(nonce,seen_ts) VALUES (?,?)",
                       (nonce,int(time.time())))
            db.commit()
        os.chmod(p, 0o600)
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise OwnerGatewayDenied("gateway nonce rejected or store unavailable") from exc


class SignedGatewayOwnerMiddleware:
    """ASGI signature verifier. Enabled only with explicit configuration."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/api/tasks" \
                or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        # An upstream mutable scope principal must never survive a failed gate.
        state = scope.setdefault("state", {})
        if not isinstance(state, dict):
            await JSONResponse({"detail":"owner scope invalid"},status_code=401)(
                scope,receive,send)
            return
        state.pop("verified_owner", None)
        if os.getenv("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY") != "true" \
                or os.getenv("ASSISTX_REQUIRE_OWNER_BINDING") != "true":
            await JSONResponse({"detail":"owner gateway not enabled"},status_code=503)(
                scope,receive,send)
            return
        headers = [v for k,v in scope.get("headers",[]) if k.lower()==HEADER]
        if len(headers) != 1 or len(headers[0]) > MAX_ATTESTATION:
            await JSONResponse({"detail":"owner authorization required"},status_code=401)(
                scope,receive,send)
            return
        received = bytearray()
        complete = False
        while not complete and len(received) <= MAX_BODY:
            msg = await receive()
            if msg.get("type") != "http.request":
                break
            received.extend(msg.get("body",b""))
            complete = not msg.get("more_body",False)
        if not complete or len(received) > MAX_BODY:
            await JSONResponse({"detail":"unbounded owner request"},status_code=413)(
                scope,receive,send)
            return
        try:
            decoded = verify_attestation(headers[0].decode("ascii"),
                method="POST",path="/api/tasks",request_body=bytes(received))
            _consume_nonce(decoded["nonce"])
        except (OwnerGatewayDenied, UnicodeError):
            await JSONResponse({"detail":"owner attestation rejected"},status_code=401)(
                scope,receive,send)
            return
        state["verified_owner"] = {
            "owner_issuer":decoded["owner_issuer"],
            "owner_subject":decoded["owner_subject"],
            "authenticated":True,
        }
        sent = False
        async def replay() -> dict:
            nonlocal sent
            if sent:
                return {"type":"http.request","body":b"","more_body":False}
            sent=True
            return {"type":"http.request","body":bytes(received),"more_body":False}
        await self.app(scope,replay,send)


def install_signed_owner_gateway(app: Any) -> None:
    """Explicit opt-in only; startup fails if independent key/nonce store absent."""
    if os.getenv("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY") != "true":
        return
    _need(os.getenv("ASSISTX_REQUIRE_OWNER_BINDING") == "true",
          "cannot enable gateway without API owner enforcement")
    _public_key()
    db_path = _private_file_path(
        os.getenv("ASSISTX_OWNER_GATEWAY_NONCE_DB",""),
        file_name="gateway replay DB")
    _need(db_path.parent.is_dir() and not (db_path.parent.stat().st_mode & 0o077),
          "gateway replay DB custody not configured")
    app.add_middleware(SignedGatewayOwnerMiddleware)
