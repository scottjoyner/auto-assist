from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

_INTERNAL_IDENTITY_HEADER = b"x-assistx-executor-identity"
_BOOTSTRAP_PATHS = (
    ("GET", re.compile(r"^/api/agent/tasks$")),
    ("POST", re.compile(r"^/api/tasks/[^/]+/claim$")),
)
_TASK_PATHS = (
    ("POST", re.compile(r"^/api/brain/context$"), "context"),
    ("POST", re.compile(r"^/api/tasks/([^/]+)/heartbeat$"), "heartbeat"),
    ("POST", re.compile(r"^/api/tasks/([^/]+)/complete$"), "complete"),
)


class ExecutorTokenError(ValueError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise ExecutorTokenError("executor token contains invalid base64") from exc


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _read_secret(file_env: str, value_env: str) -> bytes:
    path = os.getenv(file_env, "").strip()
    if path:
        with open(path, "rb") as handle:
            return handle.read()
    value = os.getenv(value_env, "")
    return value.replace("\\n", "\n").encode("utf-8") if value else b""


def _load_private_key() -> Ed25519PrivateKey:
    raw = _read_secret(
        "ASSISTX_EXECUTOR_SIGNING_KEY_FILE",
        "ASSISTX_EXECUTOR_SIGNING_KEY_PEM",
    )
    if not raw:
        raise ExecutorTokenError("AssistX executor signing key is not configured")
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except ValueError:
        try:
            key = Ed25519PrivateKey.from_private_bytes(_b64decode(raw.decode("ascii")))
        except Exception as exc:
            raise ExecutorTokenError("AssistX executor signing key is invalid") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ExecutorTokenError("AssistX executor signing key must be Ed25519")
    return key


def _load_public_key() -> Ed25519PublicKey:
    raw = _read_secret(
        "ASSISTX_EXECUTOR_VERIFY_KEY_FILE",
        "ASSISTX_EXECUTOR_VERIFY_KEY_PEM",
    )
    if raw:
        try:
            key = serialization.load_pem_public_key(raw)
        except ValueError:
            try:
                key = Ed25519PublicKey.from_public_bytes(_b64decode(raw.decode("ascii")))
            except Exception as exc:
                raise ExecutorTokenError("AssistX executor verification key is invalid") from exc
        if not isinstance(key, Ed25519PublicKey):
            raise ExecutorTokenError("AssistX executor verification key must be Ed25519")
        return key
    return _load_private_key().public_key()


class ExecutorTokenCodec:
    def __init__(
        self,
        *,
        private_key: Ed25519PrivateKey | None = None,
        public_key: Ed25519PublicKey | None = None,
        key_id: str | None = None,
    ) -> None:
        self.private_key = private_key
        self.public_key = public_key or (private_key.public_key() if private_key else None)
        self.key_id = str(key_id or os.getenv("ASSISTX_EXECUTOR_KEY_ID", "assistx-executor-v1"))

    @classmethod
    def signer_from_env(cls) -> "ExecutorTokenCodec":
        return cls(private_key=_load_private_key())

    @classmethod
    def verifier_from_env(cls) -> "ExecutorTokenCodec":
        return cls(public_key=_load_public_key())

    def encode(self, claims: Mapping[str, Any]) -> str:
        if self.private_key is None:
            raise ExecutorTokenError("executor token signer has no private key")
        header = {
            "alg": "EdDSA",
            "kid": self.key_id,
            "typ": "assistx-executor+jwt",
        }
        encoded_header = _b64encode(_json_bytes(header))
        encoded_claims = _b64encode(_json_bytes(claims))
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        return f"{encoded_header}.{encoded_claims}.{_b64encode(self.private_key.sign(signing_input))}"

    def decode(
        self,
        token: str,
        *,
        audience: str,
        now: int | None = None,
    ) -> dict[str, Any]:
        if self.public_key is None:
            raise ExecutorTokenError("executor token verifier has no public key")
        parts = str(token or "").split(".")
        if len(parts) != 3:
            raise ExecutorTokenError("executor token must contain three segments")
        encoded_header, encoded_claims, encoded_signature = parts
        try:
            header = json.loads(_b64decode(encoded_header))
            claims = json.loads(_b64decode(encoded_claims))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExecutorTokenError("executor token JSON is invalid") from exc
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise ExecutorTokenError("executor token payload is invalid")
        if header.get("alg") != "EdDSA" or header.get("typ") != "assistx-executor+jwt":
            raise ExecutorTokenError("executor token header is not supported")
        if str(header.get("kid") or "") != self.key_id:
            raise ExecutorTokenError("executor token key id is not accepted")
        try:
            self.public_key.verify(
                _b64decode(encoded_signature),
                f"{encoded_header}.{encoded_claims}".encode("ascii"),
            )
        except Exception as exc:
            raise ExecutorTokenError("executor token signature is invalid") from exc

        current = int(now if now is not None else time.time())
        issued_at = int(claims.get("iat") or 0)
        not_before = int(claims.get("nbf") or issued_at)
        expires = int(claims.get("exp") or 0)
        if not issued_at or issued_at > current + 30:
            raise ExecutorTokenError("executor token issuance time is invalid")
        if not_before > current + 5:
            raise ExecutorTokenError("executor token is not active")
        if expires <= current:
            raise ExecutorTokenError("executor token is expired")
        audiences = claims.get("aud") or []
        if isinstance(audiences, str):
            audiences = [audiences]
        if audience not in audiences:
            raise ExecutorTokenError("executor token audience is not accepted")
        if claims.get("iss") != "assistx":
            raise ExecutorTokenError("executor token issuer is not accepted")
        required = ("task_id", "claim_id", "agent_id", "jti")
        if not all(str(claims.get(item) or "").strip() for item in required):
            raise ExecutorTokenError("executor token is missing required identity claims")
        return claims


def _bearer(headers: list[tuple[bytes, bytes]]) -> str:
    for key, value in headers:
        if key.lower() == b"authorization":
            text = value.decode("latin-1").strip()
            if text.lower().startswith("bearer "):
                return text[7:].strip()
    return ""


def _replace_internal_header(
    headers: list[tuple[bytes, bytes]],
    identity: str | None,
) -> list[tuple[bytes, bytes]]:
    result = [
        (key, value)
        for key, value in headers
        if key.lower() != _INTERNAL_IDENTITY_HEADER
    ]
    if identity:
        result.append((_INTERNAL_IDENTITY_HEADER, identity.encode("utf-8")))
    return result


def _bootstrap_route(method: str, path: str) -> bool:
    return any(method == expected and pattern.match(path) for expected, pattern in _BOOTSTRAP_PATHS)


def _task_route(method: str, path: str) -> tuple[str, str | None] | None:
    for expected, pattern, scope_name in _TASK_PATHS:
        match = pattern.match(path)
        if method == expected and match:
            task_id = match.group(1) if match.lastindex else None
            return scope_name, task_id
    return None


async def _read_body(receive: Callable[[], Any]) -> tuple[bytes, Callable[[], Any]]:
    body = bytearray()
    more = True
    while more:
        message = await receive()
        body.extend(message.get("body", b""))
        more = bool(message.get("more_body", False))
    sent = False

    async def replay() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    return bytes(body), replay


class ExecutorAuthMiddleware:
    """Permit executor bearer credentials only on the narrow task API surface.

    Basic/operator authentication remains available for humans. Incoming callers
    can never set the internal trusted identity header directly; this middleware
    strips it and re-adds it only after a bootstrap or task token is verified.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "")
        headers = list(scope.get("headers") or [])
        token = _bearer(headers)
        identity: str | None = None
        replay = receive

        if token and _bootstrap_route(method, path):
            expected = os.getenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "").strip()
            if not expected or not hmac.compare_digest(token, expected):
                await JSONResponse(status_code=401, content={"detail": "invalid executor bootstrap token"})(scope, receive, send)
                return
            identity = "executor-bootstrap"
        elif token and (route := _task_route(method, path)) is not None:
            body, replay = await _read_body(receive)
            try:
                claims = ExecutorTokenCodec.verifier_from_env().decode(
                    token,
                    audience="assistx-executor",
                )
                scope_name, path_task_id = route
                scopes = {str(item) for item in claims.get("scopes") or []}
                if scope_name not in scopes:
                    raise ExecutorTokenError(f"executor token lacks {scope_name} scope")
                task_id = path_task_id
                if task_id is None and body:
                    parsed = json.loads(body)
                    task_id = str(parsed.get("task_id") or "") if isinstance(parsed, dict) else ""
                if not task_id or task_id != str(claims.get("task_id")):
                    raise ExecutorTokenError("executor token task does not match request")
                identity = f"executor:{claims['agent_id']}:{claims['claim_id']}"
            except (ExecutorTokenError, json.JSONDecodeError) as exc:
                await JSONResponse(status_code=401, content={"detail": str(exc)})(scope, replay, send)
                return
        elif token and path.startswith(("/api/agent/", "/api/tasks/", "/api/brain/context")):
            await JSONResponse(status_code=403, content={"detail": "executor bearer token is not valid for this endpoint"})(scope, receive, send)
            return

        updated = dict(scope)
        updated["headers"] = _replace_internal_header(headers, identity)
        await self.app(updated, replay, send)


class ExecutorTokenRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=300)
    claim_id: str = Field(min_length=1, max_length=300)


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            value = decoded
        except json.JSONDecodeError:
            value = [item.strip() for item in value.split(",")]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _bootstrap_authorized(authorization: str | None) -> bool:
    expected = os.getenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "").strip()
    supplied = str(authorization or "").strip()
    return bool(expected and supplied.lower().startswith("bearer ") and hmac.compare_digest(supplied[7:].strip(), expected))


def _claim_status_authorized(authorization: str | None) -> bool:
    expected = os.getenv("ASSISTX_EXECUTOR_CLAIM_STATUS_TOKEN", "").strip()
    supplied = str(authorization or "").strip()
    return bool(
        expected
        and supplied.lower().startswith("bearer ")
        and hmac.compare_digest(supplied[7:].strip(), expected)
    )


def build_executor_security_router(neo_factory: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/executor", tags=["executor-security"])

    @router.get("/claims/{task_id}/status")
    def claim_status(
        task_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Return the current claim/projection binding for auto-router fencing."""

        if not _claim_status_authorized(authorization):
            raise HTTPException(status_code=401, detail="invalid claim-status token")
        neo = neo_factory()
        try:
            with neo._session() as session:
                row = session.run(
                    """
                    MATCH (t:Task {id:$task_id})
                    OPTIONAL MATCH (s:FleetProjectionState {name:'canonical'})
                    RETURN properties(t) AS task,
                           s.generation AS projection_generation,
                           s.status AS projection_status,
                           s.expires_at_ts AS projection_expires_at_ts
                    LIMIT 1
                    """,
                    {"task_id": task_id},
                ).single()
        finally:
            neo.close()
        if not row:
            return {"active": False, "task_id": task_id, "reason": "not_found"}
        task = dict(row.get("task") or {})
        now_ms = int(time.time() * 1000)
        claim_id = str(task.get("claim_id") or task.get("active_claim_id") or "")
        agent_id = str(task.get("claimed_by") or task.get("agent_id") or "")
        status = str(task.get("status") or "").upper()
        lease_expiry = int(
            task.get("lease_expires_at_ts")
            or task.get("claim_expires_at_ts")
            or task.get("lease_until_ts")
            or 0
        )
        generation = int(row.get("projection_generation") or 0)
        projection_status = str(row.get("projection_status") or "").lower()
        projection_expiry = int(row.get("projection_expires_at_ts") or 0)
        reason = "active"
        active = True
        if status not in {"CLAIMED", "RUNNING", "PAUSING"}:
            active, reason = False, "task_not_active"
        elif not claim_id or not agent_id:
            active, reason = False, "claim_identity_missing"
        elif lease_expiry <= now_ms:
            active, reason = False, "claim_expired"
        elif projection_status != "approved" or generation <= 0:
            active, reason = False, "projection_not_approved"
        elif projection_expiry <= now_ms:
            active, reason = False, "projection_expired"
        return {
            "active": active,
            "reason": reason,
            "task_id": task_id,
            "claim_id": claim_id,
            "agent_id": agent_id,
            "lease_expires_at_ts": lease_expiry,
            "projection_generation": generation,
            "projection_expires_at_ts": projection_expiry,
        }

    @router.post("/claims/{task_id}/token")
    def issue_task_token(
        task_id: str,
        body: ExecutorTokenRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not _bootstrap_authorized(authorization):
            raise HTTPException(status_code=401, detail="invalid executor bootstrap token")
        neo = neo_factory()
        try:
            with neo._session() as session:
                row = session.run(
                    """
                    MATCH (t:Task {id:$task_id})
                    WHERE toUpper(coalesce(t.status, '')) IN ['CLAIMED','RUNNING']
                    OPTIONAL MATCH (s:FleetProjectionState {name:'canonical'})
                    RETURN properties(t) AS task,
                           s.generation AS projection_generation,
                           s.status AS projection_status,
                           s.expires_at_ts AS projection_expires_at_ts
                    LIMIT 1
                    """,
                    {"task_id": task_id},
                ).single()
        finally:
            neo.close()
        if not row:
            raise HTTPException(status_code=409, detail="task is not actively claimed")
        task = dict(row.get("task") or {})
        stored_claim = str(task.get("claim_id") or task.get("active_claim_id") or "")
        stored_agent = str(task.get("claimed_by") or task.get("agent_id") or "")
        if stored_claim != body.claim_id or stored_agent != body.agent_id:
            raise HTTPException(status_code=409, detail="claim ownership does not match")

        now_ms = int(time.time() * 1000)
        lease_expiry_ms = int(
            task.get("lease_expires_at_ts")
            or task.get("claim_expires_at_ts")
            or task.get("lease_until_ts")
            or 0
        )
        if lease_expiry_ms <= now_ms:
            raise HTTPException(status_code=409, detail="claim lease is expired or missing")
        generation = int(row.get("projection_generation") or 0)
        if str(row.get("projection_status") or "").lower() != "approved" or generation <= 0:
            raise HTTPException(status_code=409, detail="approved runtime projection is unavailable")
        projection_expiry = int(row.get("projection_expires_at_ts") or 0)
        if projection_expiry <= now_ms:
            raise HTTPException(status_code=409, detail="runtime projection approval is expired")

        payload = _json_mapping(task.get("payload_json") or task.get("payload"))
        contract = _json_mapping(payload.get("execution_contract"))
        model_aliases = _string_list(payload.get("allowed_model_aliases"))
        if not model_aliases:
            model_aliases = [str(payload.get("model") or "auto/code")]
        # Operator-approved fleet defaults ride along so adapters whose runtime
        # model differs from the task-declared alias are not 401'd by their own
        # token scope. Comma-separated, empty by default.
        model_aliases += [
            a.strip() for a in
            os.getenv("ASSISTX_EXECUTOR_DEFAULT_MODEL_ALIASES", "").split(",")
            if a.strip()
        ]
        configured_tools = {
            item.strip()
            for item in os.getenv(
                "ASSISTX_EXECUTOR_ALLOWED_TOOLS",
                "terminal,file,code_execution,skills,memory,todo",
            ).split(",")
            if item.strip()
        }
        requested_tools = set(_string_list(payload.get("allowed_tools"))) or configured_tools
        allowed_tools = sorted(requested_tools & configured_tools)
        repository = str(contract.get("repository") or payload.get("repository") or "")
        allowed_paths = _string_list(contract.get("allowed_paths") or payload.get("allowed_paths"))

        ttl_seconds = max(30, min(int(os.getenv("ASSISTX_EXECUTOR_TOKEN_TTL_SECONDS", "600")), 1800))
        expires_ms = min(lease_expiry_ms, projection_expiry, now_ms + ttl_seconds * 1000)
        if expires_ms <= now_ms + 5000:
            raise HTTPException(status_code=409, detail="claim does not have enough remaining lease time")
        now = now_ms // 1000
        claims = {
            "iss": "assistx",
            "aud": ["assistx-executor", "auto-router"],
            "iat": now,
            "nbf": now,
            "exp": expires_ms // 1000,
            "jti": str(uuid.uuid4()),
            "task_id": task_id,
            "claim_id": body.claim_id,
            "agent_id": body.agent_id,
            "projection_generation": generation,
            "scopes": ["context", "heartbeat", "complete", "inference"],
            "allowed_model_aliases": sorted(set(model_aliases)),
            "allowed_tools": allowed_tools,
            "repository": repository,
            "allowed_paths": allowed_paths,
            "max_input_tokens": max(1024, int(os.getenv("ASSISTX_EXECUTOR_MAX_INPUT_TOKENS", "65536"))),
            "max_output_tokens": max(128, int(os.getenv("ASSISTX_EXECUTOR_MAX_OUTPUT_TOKENS", "8192"))),
            "max_attempts": max(1, int(os.getenv("ASSISTX_EXECUTOR_MAX_INFERENCE_ATTEMPTS", "32"))),
        }
        token = ExecutorTokenCodec.signer_from_env().encode(claims)
        return {
            "token": token,
            "token_type": "Bearer",
            "expires_at": claims["exp"],
            "claims": {key: value for key, value in claims.items() if key != "jti"},
        }

    return router


def install_executor_security(app: Any, neo_factory: Callable[[], Any], auth_module: Any) -> None:
    if getattr(app.state, "executor_security_installed", False):
        return
    # The legacy auth dependency reads this module variable at request time. The
    # middleware removes caller-supplied values and injects it only after executor
    # authentication succeeds.
    auth_module.TRUSTED_AUTH_HEADER = _INTERNAL_IDENTITY_HEADER.decode("ascii")
    app.add_middleware(ExecutorAuthMiddleware)
    app.include_router(build_executor_security_router(neo_factory))
    app.state.executor_security_installed = True
