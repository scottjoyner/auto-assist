from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, HTTPException

from . import runtime_projection as legacy


_DEFAULT_KEY_ID = "assistx-runtime-projection-v1"
_INTERNAL_COMPAT_SECRET = "assistx-ed25519-projection-wrapper"


class RuntimeProjectionSigningError(RuntimeError):
    pass


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise RuntimeProjectionSigningError(
            "runtime projection private key is not valid base64url"
        ) from exc


def _private_key_bytes_from_env() -> bytes:
    path_value = os.getenv(
        "ASSISTX_RUNTIME_PROJECTION_SIGNING_KEY_FILE",
        "",
    ).strip()
    if path_value:
        path = Path(path_value).expanduser().resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeProjectionSigningError(
                "runtime projection signing key file must be a regular nonsymlinked file"
            )
        if path.stat().st_mode & 0o077:
            raise RuntimeProjectionSigningError(
                "runtime projection signing key file must be mode 0600 or stricter"
            )
        return path.read_bytes()
    value = os.getenv("ASSISTX_RUNTIME_PROJECTION_SIGNING_KEY_PEM", "")
    return value.replace("\\n", "\n").encode("utf-8") if value else b""


def load_private_key() -> Ed25519PrivateKey:
    raw = _private_key_bytes_from_env()
    if not raw:
        raise RuntimeProjectionSigningError(
            "runtime projection Ed25519 signing key is required"
        )
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except ValueError:
        try:
            key = Ed25519PrivateKey.from_private_bytes(
                _b64url_decode(raw.decode("ascii"))
            )
        except Exception as exc:
            raise RuntimeProjectionSigningError(
                "runtime projection signing key is invalid"
            ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeProjectionSigningError(
            "runtime projection signing key must be Ed25519"
        )
    return key


def signing_message(document: dict[str, Any]) -> bytes:
    payload = {
        "schema_version": str(document.get("schema_version") or ""),
        "source": str(document.get("source") or ""),
        "generation": int(document.get("generation") or 0),
        "revision": str(document.get("revision") or ""),
        "checksum": str(document.get("checksum") or ""),
        "generated_at_ms": int(document.get("generated_at_ms") or 0),
        "expires_at_ms": int(document.get("expires_at_ms") or 0),
        "signature_algorithm": str(
            document.get("signature_algorithm") or ""
        ),
        "signature_key_id": str(document.get("signature_key_id") or ""),
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def build_runtime_projection_v2(
    neo_factory: Callable[[], Any],
    *,
    private_key: Ed25519PrivateKey | None = None,
    key_id: str | None = None,
    ttl_seconds: int = 60,
    now_ms: int | None = None,
) -> dict[str, Any]:
    document = legacy.build_runtime_projection(
        neo_factory,
        secret=_INTERNAL_COMPAT_SECRET,
        ttl_seconds=ttl_seconds,
        now_ms=now_ms,
    )
    document.pop("signature", None)
    document["schema_version"] = "2"
    document["signature_algorithm"] = "Ed25519"
    document["signature_key_id"] = (
        key_id
        or os.getenv(
            "ASSISTX_RUNTIME_PROJECTION_SIGNING_KEY_ID",
            _DEFAULT_KEY_ID,
        ).strip()
        or _DEFAULT_KEY_ID
    )
    document["checksum"] = legacy.projection_checksum(document)
    signer = private_key or load_private_key()
    document["signature"] = _b64url_encode(
        signer.sign(signing_message(document))
    )
    return document


def build_runtime_projection_router_v2(
    neo_factory: Callable[[], Any],
    auth_dependency: Any | None = None,
) -> APIRouter:
    dependencies = (
        [Depends(auth_dependency)] if auth_dependency is not None else []
    )
    router = APIRouter(
        prefix="/api/router",
        tags=["auto-router"],
        dependencies=dependencies,
    )

    @router.get("/runtime-projection")
    def runtime_projection() -> dict[str, Any]:
        try:
            return build_runtime_projection_v2(
                neo_factory,
                ttl_seconds=int(
                    os.getenv(
                        "ASSISTX_RUNTIME_PROJECTION_TTL_SECONDS",
                        "60",
                    )
                ),
            )
        except (
            legacy.RuntimeProjectionBlocked,
            RuntimeProjectionSigningError,
            ValueError,
        ) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router
