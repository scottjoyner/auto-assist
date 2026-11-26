from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import stat
from pathlib import Path
import time
import uuid
from email.header import decode_header, make_header
from typing import Any, Callable, Iterable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field
import httpx


_TAILSCALE_LOGIN_HEADER = "Tailscale-User-Login"
_mobile_security = HTTPBasic(auto_error=False)


class MobileAgentMessageIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str = Field(pattern="^(system|user|assistant|tool)$")
    content: str = Field(min_length=1, max_length=64_000)


class MobileAgentChatIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(default="hermes-agent", max_length=256)
    messages: list[MobileAgentMessageIn] = Field(min_length=1, max_length=80)
    stream: bool = True


class MobileModelChatIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model_handle: str = Field(pattern=r"^model:v1:[0-9a-f]{32}$")
    messages: list[MobileAgentMessageIn] = Field(min_length=1, max_length=80)
    stream: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=32768)


def _decode_identity_header(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _trusted_login_header_name() -> str:
    """Return the canonical Tailnet login header only when explicitly enabled.

    Executor security intentionally rewrites the legacy api module's
    TRUSTED_AUTH_HEADER variable to its own internal identity header. The mobile
    boundary must not depend on that mutable module variable. Instead it reads
    the deployment environment directly and only accepts Tailscale's canonical
    login header name. Any other configured trusted header fails closed for the
    mobile SSO path and leaves legacy Basic auth as the fallback.
    """

    configured = os.getenv("TRUSTED_AUTH_HEADER", "").strip()
    if configured.lower() != _TAILSCALE_LOGIN_HEADER.lower():
        return ""
    return _TAILSCALE_LOGIN_HEADER


def _configured_tailnet_logins() -> set[str]:
    configured = {
        item.strip().lower()
        for item in os.getenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "").split(",")
        if item.strip()
    }
    if configured:
        return configured

    # Existing production containers may predate the Compose variable. Permit
    # a durable, operator-owned allowlist in the already-mounted fleet-state
    # directory so a controlled restart can remain fail-closed without
    # recreating the container or widening its environment.
    state_dir = Path(
        os.getenv("ASSISTX_FLEET_STATE_DIR", "/app/fleet-state")
    ).expanduser()
    allowlist_path = state_dir / "kipnerter-mobile-allowlist.txt"
    try:
        raw = allowlist_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {
        item.strip().lower()
        for line in raw.splitlines()
        for item in line.split(",")
        if item.strip()
    }


def _allowed_tailnet_login(login: str) -> bool:
    # An unconfigured allowlist must never authorize a Tailnet user on the
    # mobile agent/LLM boundary. Tailnet membership is not itself a grant to
    # execute Hermes; deployment must explicitly approve logins.
    configured = _configured_tailnet_logins()
    return bool(configured) and login.strip().lower() in configured


def _tailnet_identity(request: Request) -> tuple[str, str] | None:
    header_name = _trusted_login_header_name()
    if not header_name:
        return None
    raw_login = request.headers.get(header_name, "").strip()
    if not raw_login:
        return None
    login = _decode_identity_header(raw_login)
    if not _allowed_tailnet_login(login):
        raise HTTPException(status_code=403, detail="Tailnet identity is not authorized for Kipnerter")
    display_name = _decode_identity_header(
        request.headers.get("Tailscale-User-Name", "").strip()
    ) or login
    return login, display_name


def _require_evidence_peer(request: Request) -> None:
    """The evidence lane must originate at an explicitly provisioned proxy peer."""
    configured = os.getenv("KIPNERTER_EVIDENCE_PROXY_PEERS", "").strip()
    if not configured:
        state = Path(os.getenv("ASSISTX_FLEET_STATE_DIR", "/app/fleet-state"))
        try:
            configured = (state / "kipnerter-evidence-proxy-peers.txt").read_text(encoding="utf-8")
        except OSError:
            configured = ""
    try:
        peers = {ipaddress.ip_address(v.strip()) for v in configured.replace("\n", ",").split(",") if v.strip()}
        observed = ipaddress.ip_address(request.client.host) if request.client else None
    except ValueError:
        peers, observed = set(), None
    if observed not in peers or not peers:
        raise HTTPException(status_code=403, detail={"error": "evidence_proxy_peer_required"})


def _account_id(login: str) -> str:
    digest = hashlib.sha256(login.strip().lower().encode("utf-8")).hexdigest()[:24]
    return f"tailscale:{digest}"


def _prompt_from_messages(messages: Iterable[MobileAgentMessageIn]) -> str:
    sections: list[str] = [
        "You are serving the authenticated Kipnerter mobile assistant conversation below.",
        "Preserve the supplied conversation context. Answer the latest user request directly.",
    ]
    for message in messages:
        sections.append(f"[{message.role.upper()}]\n{message.content}")
    return "\n\n".join(sections)


def _requested_hermes_model(alias: str) -> Optional[str]:
    normalized = alias.strip()
    if normalized.lower() in {"", "auto", "agent:auto", "hermes-agent", "fleet-auto"}:
        return None
    if os.getenv("KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE", "0").strip().lower() not in {"1", "true", "yes"}:
        return None
    return normalized


def _agent_timeout() -> int:
    try:
        value = int(os.getenv("KIPNERTER_AGENT_TIMEOUT", "300"))
    except ValueError:
        value = 300
    return max(30, min(value, 900))


def _run_hermes(
    prompt: str,
    *,
    timeout: int,
    model: Optional[str],
    provider: str,
) -> dict[str, Any]:
    # Import lazily so API startup and contract tests do not require the Hermes
    # executable. The executor itself remains server-side and receives its own
    # claim-scoped router credentials; no executor token is ever sent to iOS.
    from .agents.hermes_agent_adapter import run_hermes

    return run_hermes(prompt, timeout=timeout, model=model, provider=provider)


def _mobile_agent_failure_code(result: dict[str, Any]) -> str:
    """Map known fleet-admission failures to stable mobile-safe error codes."""
    diagnostic = "\n".join(
        str(result.get(key) or "") for key in ("output", "stderr")
    ).lower()
    projection_markers = (
        "runtime_projection_unavailable",
        "signed assistx runtime projection is not configured",
        "runtime projection approval evidence is expired",
    )
    if any(marker in diagnostic for marker in projection_markers):
        return "runtime_projection_unavailable"
    if result.get("error") == "executor_task_token_missing" or any(marker in diagnostic for marker in (
        "executor_task_token_missing", "invalid executor bootstrap token",
        "executor token must contain three segments", "executor token expired",
        "executor token invalid", "invalid executor token",
    )):
        return "executor_authentication_failed"
    return str(result.get("error") or "hermes_execution_failed")[:240]


def _openai_response(output: str, model: str, session_id: str) -> dict[str, Any]:
    return {
        "id": session_id,
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": output},
                "finish_reason": "stop",
            }
        ],
    }


def _sse_chunks(output: str, model: str, session_id: str, chunk_size: int = 256):
    for offset in range(0, len(output), chunk_size):
        payload = {
            "id": session_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": output[offset : offset + chunk_size]},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"



_DEFAULT_MOBILE_HANDLE_SECRET_FILE = "/run/secrets/kipnerter_mobile_model_handle"


def _configured_mobile_handle_secret() -> str:
    """Use explicit environment configuration or a private mounted secret file.

    Do not create or rotate a key on read. Missing, insecure, or malformed
    files leave model handles unavailable rather than minting unstable IDs.
    """
    configured = os.getenv("KIPNERTER_MOBILE_MODEL_HANDLE_SECRET", "").strip()
    if configured:
        return configured
    path = os.getenv("KIPNERTER_MOBILE_MODEL_HANDLE_SECRET_FILE", _DEFAULT_MOBILE_HANDLE_SECRET_FILE).strip()
    if not path or not os.path.isabs(path):
        return ""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                return ""
            if not 1 <= metadata.st_size <= 4096:
                return ""
            raw = handle.read(4097)
            if len(raw) > 4096:
                return ""
            secret = raw.decode("utf-8").strip()
            return secret if len(secret) >= 32 and not any(ch.isspace() or ord(ch) < 33 for ch in secret) else ""
    except (OSError, UnicodeError):
        return ""


def _mobile_model_handle(
    model: dict[str, Any],
    *,
    secret: str | None = None,
) -> str | None:
    """Derive a stable opaque mobile handle from admitted artifact identity.

    Handles are keyed so a public/known artifact fingerprint cannot be mapped to
    the mobile identifier by an offline dictionary. The version prefix gives us
    an explicit future rotation/migration boundary. Physical node/runtime/route
    identity is deliberately excluded so replica movement does not change the
    handle.
    """

    fingerprint = str(model.get("artifact_fingerprint") or "").strip().lower()
    if not fingerprint:
        return None
    handle_secret = (
        secret
        if secret is not None
        else _configured_mobile_handle_secret()
    ).strip()
    if not handle_secret:
        return None
    digest = hmac.new(
        handle_secret.encode("utf-8"),
        ("assistx-mobile-model-handle-v1\0" + fingerprint).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"model:v1:{digest}"


def _mobile_model_display_name(value: Any) -> str:
    # Artifact aliases sometimes originate from a local filename rather than
    # a presentation label. Never disclose the parent directory to a phone.
    name = str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    if name.lower().endswith(".gguf"):
        name = name[:-5].strip()
    return name[:160] if name not in {"", ".", ".."} else "Fleet model"


def _sanitize_runtime_projection_for_mobile(
    projection: dict[str, Any],
    *,
    handle_secret: str | None = None,
) -> dict[str, Any]:
    """Return the minimum fleet/model metadata useful to an authenticated phone.

    The authoritative runtime projection contains internal routing coordinates,
    runtime/model instance identifiers, artifact fingerprints, and network access
    paths. Those stay server-side. Mobile receives opaque runtime/model handles,
    presentation names, readiness, and coarse capabilities only.
    """

    runtimes: list[dict[str, Any]] = []
    model_total = 0
    agent_total = 0
    code_total = 0
    model_accumulators: dict[str, dict[str, Any]] = {}

    for provider in projection.get("providers") or []:
        if not isinstance(provider, dict) or provider.get("enabled") is False:
            continue
        models = [
            item
            for item in (provider.get("models") or [])
            if isinstance(item, dict)
        ]
        if not models:
            continue

        node_id = str(provider.get("node_id") or "")
        runtime_instance_id = str(provider.get("runtime_instance_id") or "")
        runtime_name = str(provider.get("name") or "")
        opaque_seed = "|".join((node_id, runtime_instance_id, runtime_name))
        opaque_id = hashlib.sha256(opaque_seed.encode("utf-8")).hexdigest()[:20]
        opaque_runtime_id = f"runtime:{opaque_id}"

        agent_capable = bool(provider.get("allow_agent_runtime")) or any(
            bool(model.get("allow_agent_runtime")) for model in models
        )
        code_capable = bool(provider.get("allow_code_execution")) or any(
            bool(model.get("allow_code_execution")) for model in models
        )
        capabilities = sorted(
            {
                str(capability)
                for model in models
                for capability in (model.get("capabilities") or [])
                if str(capability).strip()
            }
        )
        runtime_kind = str(
            provider.get("runtime_kind") or provider.get("type") or "runtime"
        ).strip() or "runtime"

        runtimes.append(
            {
                "runtime_id": opaque_runtime_id,
                "kind": runtime_kind,
                "model_count": len(models),
                "agent_capable": agent_capable,
                "code_execution_capable": code_capable,
                "capabilities": capabilities,
            }
        )

        for model in models:
            handle = _mobile_model_handle(model, secret=handle_secret)
            if handle is None:
                # A signed production projection should already contain complete
                # artifact identity. If an older/incomplete record slips through,
                # keep aggregate visibility but do not mint an unstable handle.
                continue
            display_name = _mobile_model_display_name(model.get("alias"))
            if not display_name:
                continue
            accumulator = model_accumulators.setdefault(
                handle,
                {
                    "display_names": set(),
                    "capabilities": set(),
                    "runtime_ids": set(),
                    "agent_capable": False,
                    "code_execution_capable": False,
                },
            )
            accumulator["display_names"].add(display_name)
            accumulator["capabilities"].update(
                str(capability)
                for capability in (model.get("capabilities") or [])
                if str(capability).strip()
            )
            accumulator["runtime_ids"].add(opaque_runtime_id)
            accumulator["agent_capable"] = bool(
                accumulator["agent_capable"]
                or provider.get("allow_agent_runtime")
                or model.get("allow_agent_runtime")
            )
            accumulator["code_execution_capable"] = bool(
                accumulator["code_execution_capable"]
                or provider.get("allow_code_execution")
                or model.get("allow_code_execution")
            )

        model_total += len(models)
        agent_total += int(agent_capable)
        code_total += int(code_capable)

    mobile_models: list[dict[str, Any]] = []
    for handle, accumulator in model_accumulators.items():
        display_name = sorted(
            accumulator["display_names"],
            key=lambda value: value.casefold(),
        )[0]
        mobile_models.append(
            {
                "model_handle": handle,
                "display_name": display_name,
                "state": "ready",
                "ready_runtime_count": len(accumulator["runtime_ids"]),
                "agent_capable": bool(accumulator["agent_capable"]),
                "code_execution_capable": bool(
                    accumulator["code_execution_capable"]
                ),
                "capabilities": sorted(accumulator["capabilities"]),
            }
        )

    runtimes.sort(key=lambda item: (item["kind"], item["runtime_id"]))
    mobile_models.sort(
        key=lambda item: (
            item["display_name"].casefold(),
            item["model_handle"],
        )
    )
    runtime_total = len(runtimes)
    return {
        "schema_version": "2",
        "source": "assistx-runtime-projection",
        "generated_at_ms": projection.get("generated_at_ms"),
        "expires_at_ms": projection.get("expires_at_ms"),
        "fleet_runtime_count": runtime_total,
        "fleet_model_count": model_total,
        "fleet_unique_model_count": len(mobile_models),
        "agent_runtime_count": agent_total,
        "code_runtime_count": code_total,
        "agent_auto_available": agent_total > 0,
        "models": mobile_models,
        "runtimes": runtimes,
    }


def _unavailable_mobile_runtime_catalog() -> dict[str, Any]:
    """Represent an expected fail-closed projection state without transport ambiguity.

    This is deliberately not a stale projection and carries no backend detail.
    The phone can keep Agent Auto selected while showing it as temporarily
    unavailable, but it still has zero approved runtimes to execute against.
    """
    return {
        "schema_version": "2",
        "source": "assistx-runtime-projection",
        "generated_at_ms": None,
        "expires_at_ms": None,
        "fleet_runtime_count": 0,
        "fleet_model_count": 0,
        "fleet_unique_model_count": 0,
        "agent_runtime_count": 0,
        "code_runtime_count": 0,
        "agent_auto_available": False,
        "models": [],
        "runtimes": [],
    }


def _current_runtime_projection() -> dict[str, Any]:
    from .api import _neo
    from .runtime_projection_v2 import build_runtime_projection

    try:
        ttl_seconds = int(
            os.getenv("ASSISTX_RUNTIME_PROJECTION_TTL_SECONDS", "900")
        )
    except ValueError:
        ttl_seconds = 900
    return build_runtime_projection(
        _neo,
        ttl_seconds=max(30, min(ttl_seconds, 3600)),
    )


def _mobile_runtime_catalog() -> dict[str, Any]:
    return _sanitize_runtime_projection_for_mobile(_current_runtime_projection())


def _resolve_mobile_model_handle(
    projection: dict[str, Any],
    model_handle: str,
    *,
    handle_secret: str | None = None,
) -> dict[str, Any] | None:
    """Resolve one opaque handle against the *current* admitted projection.

    The returned artifact fingerprint is internal-only and is never serialized
    to the phone. A stale projection or missing/rotated handle fails closed.
    """

    now_ms = int(time.time() * 1000)
    try:
        expires_at_ms = int(projection.get("expires_at_ms") or 0)
    except (TypeError, ValueError):
        return None
    if expires_at_ms <= now_ms:
        return None

    matches: list[dict[str, Any]] = []
    for provider in projection.get("providers") or []:
        if not isinstance(provider, dict) or provider.get("enabled") is False:
            continue
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            candidate_handle = _mobile_model_handle(
                model,
                secret=handle_secret,
            )
            if candidate_handle != model_handle:
                continue
            fingerprint = str(model.get("artifact_fingerprint") or "").strip()
            if not fingerprint:
                continue
            matches.append(
                {
                    "artifact_fingerprint": fingerprint,
                    "display_name": _mobile_model_display_name(model.get("alias")),
                    "capabilities": {
                        str(value)
                        for value in (model.get("capabilities") or [])
                        if str(value).strip()
                    },
                }
            )

    if not matches:
        return None
    fingerprints = {item["artifact_fingerprint"] for item in matches}
    if len(fingerprints) != 1:
        # A keyed-handle collision or inconsistent projection must never choose
        # a physical route ambiguously.
        return None
    names = sorted(
        {item["display_name"] for item in matches if item["display_name"]},
        key=str.casefold,
    )
    capabilities = sorted(
        {
            capability
            for item in matches
            for capability in item["capabilities"]
        }
    )
    return {
        "model_handle": model_handle,
        "artifact_fingerprint": next(iter(fingerprints)),
        "display_name": names[0] if names else "Fleet model",
        "ready_runtime_count": len(matches),
        "capabilities": capabilities,
    }


def _mobile_router_config() -> tuple[str, str]:
    base_url = os.getenv("FLEET_ROUTER_URL", "").strip().rstrip("/")
    token = os.getenv("FLEET_ROUTER_BEARER_TOKEN", "").strip()
    if not base_url or not token:
        raise RuntimeError("mobile fleet-model router is not configured")
    return base_url, token


def _mobile_model_upstream_failure(response: Any) -> str:
    """Map router failures to a fixed mobile-safe vocabulary, never raw text."""
    status = response.status_code
    if status in (401, 403):
        return "fleet_model_router_authentication_failed"
    if status == 429:
        return "fleet_model_capacity_unavailable"
    if status == 504:
        return "fleet_model_router_timeout"
    if status == 503:
        try:
            detail = response.json().get("detail")
        except (ValueError, TypeError, AttributeError):
            detail = None
        if isinstance(detail, dict):
            error = detail.get("error")
            if error == "executor_claim_status_unavailable":
                return "fleet_model_claim_status_unavailable"
            if error == "executor_claim_unavailable":
                return "fleet_model_claim_unavailable"
            if error == "no provider satisfies required capabilities":
                return "fleet_model_no_eligible_runtime"
            if error == "all providers failed" and detail.get("details") == []:
                return "fleet_model_no_eligible_runtime"
            if error == "all providers failed":
                reasons = detail.get("details")
                if isinstance(reasons, list) and reasons:
                    # Only classify the exact known internal claim-status failure;
                    # never pass provider diagnostics, credentials or addresses through.
                    marker = "AssistX claim-status check returned HTTP "
                    if all(isinstance(reason, str) and any(marker + code in reason for code in ("401", "403")) for reason in reasons):
                        return "fleet_model_claim_status_authentication_failed"
    return "fleet_model_unavailable"


def _mobile_model_router_payload(
    body: MobileModelChatIn,
    resolved: dict[str, Any],
    *,
    mobile_request_id: str | None = None,
) -> dict[str, Any]:
    metadata = {
        "assistx_source": True,
        "privacy": "local_only",
        "local_only": True,
        "assistx_mobile_model_handle": body.model_handle,
        "assistx_artifact_fingerprint": resolved["artifact_fingerprint"],
    }
    if mobile_request_id:
        metadata["assistx_mobile_request_id"] = mobile_request_id
        # The external request ID is opaque; durable router receipts require a UUID.
        if mobile_request_id.startswith("kmr:"):
            try:
                metadata["correlation_id"] = str(uuid.UUID(mobile_request_id[4:]))
            except ValueError:
                pass
    return {
        "model": "auto/local",
        "messages": [
            message.model_dump()
            for message in body.messages
        ],
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "stream": body.stream,
        "local_only": True,
        "allow_cloud": False,
        "metadata": metadata,
    }


def _sanitize_router_completion(
    payload: dict[str, Any],
    model_handle: str,
) -> dict[str, Any]:
    """Keep OpenAI response semantics without leaking backend coordinates."""

    sanitized: dict[str, Any] = {
        "id": str(payload.get("id") or "kipnerter-fleet-model"),
        "object": str(payload.get("object") or "chat.completion"),
        "model": model_handle,
        "choices": payload.get("choices") if isinstance(payload.get("choices"), list) else [],
    }
    if isinstance(payload.get("created"), (int, float)):
        sanitized["created"] = payload["created"]
    if isinstance(payload.get("usage"), dict):
        sanitized["usage"] = payload["usage"]
    return sanitized


async def _sanitized_router_stream(
    response: httpx.Response,
    stream_context: Any,
    client: httpx.AsyncClient,
    model_handle: str,
):
    try:
        async for line in response.aiter_lines():
            if not line:
                yield "\n"
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                yield "data: [DONE]\n\n"
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            yield (
                "data: "
                + json.dumps(
                    _sanitize_router_completion(payload, model_handle),
                    ensure_ascii=False,
                )
                + "\n\n"
            )
    finally:
        await stream_context.__aexit__(None, None, None)
        await client.aclose()


def register_mobile_agent_routes(
    router: APIRouter, auth_dependency: Callable[..., str],
    *, fleet_evidence_factory: Callable[..., Any] | None = None,
) -> None:
    def mobile_auth(
        request: Request,
        credentials: HTTPBasicCredentials | None = Depends(_mobile_security),
    ) -> str:
        """Authenticate the mobile boundary without sharing executor identity state.

        Tailscale Serve is authoritative for Agent Auto and injects the canonical
        identity header. If no Tailnet identity is present, preserve the existing
        operator Basic-auth dependency as the explicit legacy fallback.
        """

        identity = _tailnet_identity(request)
        if identity is not None:
            return identity[0]
        return auth_dependency(request, credentials)

    @router.get("/api/v1/auth/whoami", tags=["kipnerter-mobile"])
    def tailnet_whoami(
        request: Request,
        user: str = Depends(mobile_auth),
    ) -> dict[str, Any]:
        identity = _tailnet_identity(request)
        if identity is None:
            # A legacy Basic-authenticated operator may reach this route, but it
            # is intentionally not represented to the app as Tailnet SSO.
            return {
                "authenticated": False,
                "provider": "legacy",
                "login": user,
                "display_name": user,
                "account_id": f"legacy:{user}",
                "session_expires_at": None,
            }

        login, display_name = identity
        return {
            "authenticated": True,
            "provider": "tailscale",
            "login": login,
            "display_name": display_name,
            "account_id": _account_id(login),
            "session_expires_at": None,
        }

    @router.get("/api/v1/runtime/catalog", tags=["kipnerter-mobile"])
    def mobile_runtime_catalog(
        request: Request,
        user: str = Depends(mobile_auth),
    ) -> dict[str, Any]:
        # Re-evaluate Tailnet identity so an allowlist change cannot be bypassed
        # after dependency resolution. Legacy Basic auth remains an operator-only
        # fallback exactly as it is for whoami/chat.
        _tailnet_identity(request)
        try:
            return _mobile_runtime_catalog()
        except Exception as exc:
            # Projection expiry or signing unavailability is an expected
            # fail-closed admission state, not a broken mobile transport.
            # Return a sanitized zero-runtime catalog so clients can represent
            # the degraded state without ever treating stale runtimes as usable.
            # Both approval/expiry and v2 signer failures raise the canonical
            # RuntimeProjectionBlocked. Do not import a non-existent v2 exception:
            # that used to turn a deliberately fail-closed catalog into HTTP 500.
            from .runtime_projection import RuntimeProjectionBlocked

            if isinstance(exc, RuntimeProjectionBlocked):
                return _unavailable_mobile_runtime_catalog()
            raise HTTPException(
                status_code=503,
                detail={"error": "runtime_catalog_unavailable"},
            ) from exc

    @router.post("/api/v1/model/chat/completions", tags=["kipnerter-mobile"])
    async def mobile_model_chat(
        body: MobileModelChatIn,
        request: Request,
        user: str = Depends(mobile_auth),
    ):
        _tailnet_identity(request)

        try:
            projection = _current_runtime_projection()
            resolved = _resolve_mobile_model_handle(
                projection,
                body.model_handle,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "runtime_projection_unavailable"},
            ) from exc
        if resolved is None:
            raise HTTPException(
                status_code=409,
                detail={"error": "model_handle_not_ready"},
            )

        try:
            router_url, router_token = _mobile_router_config()
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "fleet_model_router_unavailable"},
            ) from exc

        mobile_request_id = f"kmr:{uuid.uuid4().hex}"
        payload = _mobile_model_router_payload(
            body,
            resolved,
            mobile_request_id=mobile_request_id,
        )
        response_headers = {
            "Cache-Control": "no-store",
            "X-Kipnerter-Model-Authority": "assistx-runtime-projection",
            "X-Kipnerter-Model-Request-ID": mobile_request_id,
        }
        headers = {
            "Authorization": f"Bearer {router_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if body.stream else "application/json",
        }
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0))
        endpoint = f"{router_url}/v1/chat/completions"

        if not body.stream:
            try:
                response = await client.post(endpoint, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                await client.aclose()
                raise HTTPException(
                    status_code=503,
                    detail={"error": "fleet_model_unavailable"},
                ) from exc
            try:
                if response.status_code >= 400:
                    raise HTTPException(
                        status_code=503,
                        detail={"error": _mobile_model_upstream_failure(response)},
                        headers=response_headers,
                    )
                decoded = response.json()
                if not isinstance(decoded, dict):
                    raise HTTPException(
                        status_code=502,
                        detail={"error": "invalid_fleet_model_response"},
                    )
                return JSONResponse(
                    content=_sanitize_router_completion(
                        decoded,
                        body.model_handle,
                    ),
                    headers=response_headers,
                )
            finally:
                await client.aclose()

        stream_context = client.stream(
            "POST",
            endpoint,
            headers=headers,
            json=payload,
        )
        try:
            response = await stream_context.__aenter__()
        except httpx.HTTPError as exc:
            await client.aclose()
            raise HTTPException(
                status_code=503,
                detail={"error": "fleet_model_unavailable"},
            ) from exc
        if response.status_code >= 400:
            try:
                await response.aread()
                error = _mobile_model_upstream_failure(response)
            except httpx.HTTPError:
                error = "fleet_model_unavailable"
            finally:
                await stream_context.__aexit__(None, None, None)
                await client.aclose()
            raise HTTPException(
                status_code=503,
                detail={"error": error},
                headers=response_headers,
            )
        return StreamingResponse(
            _sanitized_router_stream(
                response,
                stream_context,
                client,
                body.model_handle,
            ),
            media_type="text/event-stream",
            headers=response_headers,
        )

    @router.get("/api/v1/agent/capabilities", tags=["kipnerter-mobile"])
    def mobile_agent_capabilities(
        request: Request,
        user: str = Depends(mobile_auth),
    ):
        if _tailnet_identity(request) is None:
            raise HTTPException(status_code=403, detail={"error": "tailnet_identity_required"})
        _require_evidence_peer(request)
        from .mobile_agent_evidence import capability_profile
        return JSONResponse(
            content=capability_profile(evidence_configured=fleet_evidence_factory is not None),
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/api/v1/agent/chat/completions", tags=["kipnerter-mobile"])
    async def mobile_agent_chat(
        body: MobileAgentChatIn,
        request: Request,
        user: str = Depends(mobile_auth),
        x_hermes_session_id: str | None = Header(default=None),
        x_hermes_session_key: str | None = Header(default=None),
    ):
        # If this request authenticated through the trusted Tailscale header,
        # enforce the optional Kipnerter login allowlist here as well. Basic
        # auth remains available as an explicit legacy operator fallback.
        _tailnet_identity(request)

        # Only the explicitly allowlisted Tailnet identity can access the
        # aggregate fleet tool. Legacy Basic and generic chat remain unchanged.
        from .mobile_agent_evidence import (
            is_fleet_status, fleet_status, render_fleet_status,
            is_inference_status, inference_status, render_inference_status,
            is_gateway_status, gateway_health, render_gateway_health,
            is_workflow_status, workflow_status, render_workflow_status,
            is_handbook_query, render_handbook, overview_sections, render_overview,
        )
        fleet_intent = is_fleet_status(body.messages)
        inference_intent = is_inference_status(body.messages)
        gateway_intent = is_gateway_status(body.messages)
        workflow_intent = is_workflow_status(body.messages)
        handbook_intent = is_handbook_query(body.messages)
        overview = overview_sections(body.messages)
        if fleet_evidence_factory is not None and (fleet_intent or inference_intent or gateway_intent or workflow_intent or handbook_intent or overview):
            if _tailnet_identity(request) is None:
                raise HTTPException(status_code=403, detail={"error": "tailnet_identity_required"})
            _require_evidence_peer(request)
            try:
                if overview:
                    output = await asyncio.to_thread(render_overview, overview, neo_factory=fleet_evidence_factory, projection_factory=_current_runtime_projection, gateway_url=os.getenv("AUTO_ROUTER_BASE_URL", ""))
                elif fleet_intent:
                    evidence = await asyncio.to_thread(fleet_status, fleet_evidence_factory)
                    output = render_fleet_status(evidence)
                elif inference_intent:
                    projection = await asyncio.to_thread(_current_runtime_projection)
                    output = render_inference_status(inference_status(projection))
                elif workflow_intent:
                    evidence = await asyncio.to_thread(workflow_status, fleet_evidence_factory)
                    output = render_workflow_status(evidence)
                elif handbook_intent:
                    output = render_handbook(body.messages)
                else:
                    evidence = await asyncio.to_thread(gateway_health, os.getenv("AUTO_ROUTER_BASE_URL", ""))
                    output = render_gateway_health(evidence)
            except Exception:
                raise HTTPException(status_code=503, detail={"error": "fleet_evidence_unavailable"}) from None
            session_id = f"fleet-evidence:{uuid.uuid4().hex}"
            response_model = "kipnerter-agent-v1"
            headers = {
                "X-Kipnerter-Agent-Executor": "fleet-evidence",
                "X-Hermes-Session-Id": session_id,
                "Cache-Control": "no-store",
            }
            if not body.stream:
                return JSONResponse(content=_openai_response(output, response_model, session_id), headers=headers)
            return StreamingResponse(_sse_chunks(output, response_model, session_id),
                                     media_type="text/event-stream", headers=headers)

        # The mobile route must respect the same runtime admission view it
        # publishes to clients. Do not start Hermes when no agent runtime is
        # admitted, and never interpret catalog failure as permission to run.
        try:
            catalog = await asyncio.to_thread(_mobile_runtime_catalog)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "runtime_projection_unavailable", "executor": "hermes"},
            ) from exc
        if catalog.get("agent_auto_available") is not True:
            raise HTTPException(
                status_code=503,
                detail={"error": "agent_runtime_unavailable", "executor": "hermes"},
            )

        prompt = _prompt_from_messages(body.messages)
        model_override = _requested_hermes_model(body.model)
        result = await asyncio.to_thread(
            _run_hermes,
            prompt,
            timeout=_agent_timeout(),
            model=model_override,
            provider=os.getenv("HERMES_PROVIDER", "assistx-router").strip() or "assistx-router",
        )
        if not result.get("success"):
            error = _mobile_agent_failure_code(result)
            raise HTTPException(
                status_code=503,
                detail={"error": error, "executor": "hermes"},
            )

        output = str(result.get("output") or "").strip()
        if not output:
            raise HTTPException(status_code=502, detail={"error": "empty_hermes_response", "executor": "hermes"})

        server_session = str(result.get("session_id") or "").strip()
        client_session = (x_hermes_session_id or "").strip()[:128]
        session_id = server_session[:128] or client_session or "kipnerter-hermes"
        response_model = body.model.strip() or "hermes-agent"
        headers = {
            "X-Kipnerter-Agent-Executor": "hermes",
            "X-Hermes-Session-Id": session_id,
            "Cache-Control": "no-store",
        }
        if x_hermes_session_key:
            headers["X-Kipnerter-Conversation-Key"] = x_hermes_session_key.strip()[:256]

        if not body.stream:
            return JSONResponse(
                content=_openai_response(output, response_model, session_id),
                headers=headers,
            )

        return StreamingResponse(
            _sse_chunks(output, response_model, session_id),
            media_type="text/event-stream",
            headers=headers,
        )