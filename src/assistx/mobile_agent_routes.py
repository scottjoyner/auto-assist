from __future__ import annotations

import asyncio
import hashlib
import json
import os
from email.header import decode_header, make_header
from typing import Any, Callable, Iterable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field


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


def _allowed_tailnet_login(login: str) -> bool:
    configured = {
        item.strip().lower()
        for item in os.getenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "").split(",")
        if item.strip()
    }
    return not configured or login.strip().lower() in configured


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


def register_mobile_agent_routes(router: APIRouter, auth_dependency: Callable[..., str]) -> None:
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
            error = str(result.get("error") or "hermes_execution_failed")[:240]
            raise HTTPException(status_code=503, detail={"error": error, "executor": "hermes"})

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
