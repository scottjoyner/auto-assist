"""Opt-in narrow inference relay: isolated Hermes -> AssistX -> private router.

No arbitrary upstream URLs, admin paths, proxy environment, user-forwarded
headers, redirects, cookies or privileged credentials. This research-only
component is OFF unless explicitly enabled at AssistX startup. Production
routing and Hermes Docker network isolation are unchanged by this module.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter(prefix="/api/internal/hermes-router/v1", include_in_schema=False)
_UPSTREAM = "http://llm-router:8088/v1/chat/completions"
_MAX_REQUEST = 64 * 1024
_MAX_RESPONSE = 8 * 1024 * 1024
_INFLIGHT = asyncio.Semaphore(2)


def _failure(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail=code)


def _credentials(request: Request) -> str:
    if os.getenv("ASSISTX_HERMES_ROUTER_RELAY_ENABLED") != "true":
        raise _failure(404, "relay_disabled")
    expected = os.getenv("ASSISTX_HERMES_RELAY_CLIENT_TOKEN", "")
    upstream_token = os.getenv("ASSISTX_INTERNAL_SERVICE_TOKEN", "")
    # Independent client identity: never let the broad router-service token
    # double as admission from the privileged Hermes adapter.
    if (len(expected) < 32 or len(upstream_token) < 16
            or hmac.compare_digest(expected, upstream_token)):
        raise _failure(503, "relay_keys_not_configured")
    supplied = request.headers.get("authorization", "")
    if (not supplied.startswith("Bearer ")
            or len(supplied) > 256
            or not hmac.compare_digest(supplied[7:], expected)):
        raise _failure(401, "relay_unauthorized")
    if request.url.query:
        raise _failure(400, "relay_query_denied")
    return upstream_token


def _object_unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def _validate_body(raw: bytes) -> bool:
    try:
        payload = json.loads(raw, object_pairs_hook=_object_unique)
    except (ValueError, UnicodeDecodeError) as exc:
        raise _failure(400, "relay_json_invalid") from exc
    if type(payload) is not dict:
        raise _failure(422, "relay_body_invalid")
    # Initial rollout deliberately limits Hermes to plain text chat; future
    # tool-call or multimodal support requires its own scoped threat review.
    if not set(payload).issubset({
        "model", "messages", "stream", "temperature", "top_p", "max_tokens",
        "stop", "frequency_penalty", "presence_penalty",
    }):
        raise _failure(422, "relay_field_denied")
    model = payload.get("model")
    models_raw = os.getenv("ASSISTX_HERMES_RELAY_ALLOWED_MODELS", "")
    allowed_models = {name.strip() for name in models_raw.split(",") if name.strip()}
    if not allowed_models:
        raise _failure(503, "relay_model_allowlist_missing")
    msgs = payload.get("messages")
    if (not isinstance(model, str) or not 1 <= len(model) <= 128
            or type(msgs) is not list or not 1 <= len(msgs) <= 32):
        raise _failure(422, "relay_model_or_messages_invalid")
    if model not in allowed_models:
        raise _failure(403, "relay_model_not_approved")
    for message in msgs:
        if (type(message) is not dict or set(message) != {"role", "content"}
                or message["role"] not in {
                    "system", "developer", "user", "assistant"
                } or not isinstance(message["content"], str)
                or len(message["content"]) > 12000):
            raise _failure(422, "relay_message_invalid")
    streamed = payload.get("stream", False)
    if type(streamed) is not bool:
        raise _failure(422, "relay_stream_invalid")
    limit = payload.get("max_tokens", 1024)
    if type(limit) is not int or not 1 <= limit <= 4096:
        raise _failure(422, "relay_token_limit_invalid")
    for key, maximum in (("temperature", 2), ("top_p", 1),
                         ("frequency_penalty", 2), ("presence_penalty", 2)):
        if key in payload:
            value = payload[key]
            if (type(value) not in (int, float) or not 0 <= value <= maximum):
                raise _failure(422, "relay_sampling_invalid")
    stop = payload.get("stop")
    if stop is not None and not (
        isinstance(stop, str) and len(stop) <= 64
        or type(stop) is list and len(stop) <= 4
        and all(isinstance(v, str) and len(v) <= 64 for v in stop)
    ):
        raise _failure(422, "relay_stop_invalid")
    return streamed


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(connect=2, read=45, write=5, pool=2),
    )


@router.post("/chat/completions")
async def chat_completions(request: Request):
    upstream_token = _credentials(request)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise _failure(415, "relay_content_type_denied")
    size_hint = request.headers.get("content-length")
    if size_hint is not None and (
        not size_hint.isdigit() or int(size_hint) > _MAX_REQUEST
    ):
        raise _failure(413, "relay_request_too_large")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > _MAX_REQUEST:
            raise _failure(413, "relay_request_too_large")
        raw.extend(chunk)
    streaming = _validate_body(bytes(raw))
    try:
        await asyncio.wait_for(_INFLIGHT.acquire(), timeout=0.1)
    except asyncio.TimeoutError as exc:
        raise _failure(429, "relay_capacity_exhausted") from exc
    client = _client()
    response = None
    stream_handoff = False
    try:
        upstream_request = client.build_request(
            "POST", _UPSTREAM, content=bytes(raw),
            headers={
                "Authorization": "Bearer " + upstream_token,
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if streaming else "application/json",
            },
        )
        response = await client.send(upstream_request, stream=True)
        if response.status_code != 200:
            raise _failure(502, "relay_upstream_rejected")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != ("text/event-stream" if streaming else "application/json"):
            raise _failure(502, "relay_upstream_media_type_invalid")
        size_hint = response.headers.get("content-length")
        if size_hint is not None and (
            not size_hint.isdigit() or int(size_hint) > _MAX_RESPONSE
        ):
            raise _failure(502, "relay_upstream_size_invalid")
        if streaming:
            stream_handoff = True
        if not streaming:
            # Fully validate non-streaming responses *before* sending the
            # first byte or 200 status to the privileged Hermes caller.
            # Partial/truncated/unbounded JSON may never be treated as success.
            received = bytearray()
            async for chunk in response.aiter_bytes():
                if len(received) + len(chunk) > _MAX_RESPONSE:
                    raise _failure(502, "relay_upstream_size_invalid")
                received.extend(chunk)
            try:
                parsed = json.loads(
                    received, object_pairs_hook=_object_unique,
                    parse_constant=lambda _constant: (
                        _ for _ in ()
                    ).throw(ValueError("nonfinite upstream JSON")),
                )
            except (ValueError, UnicodeDecodeError) as exc:
                raise _failure(502, "relay_upstream_json_invalid") from exc
            if (
                type(parsed) is not dict
                or type(parsed.get("choices")) is not list
            ):
                raise _failure(502, "relay_upstream_schema_invalid")
            return JSONResponse(
                content=parsed,
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
    except HTTPException:
        raise
    except (httpx.HTTPError, OSError, TimeoutError) as exc:
        raise _failure(503, "relay_upstream_unavailable") from exc
    finally:
        # A completed JSON response is returned above, so never leave its
        # upstream body open or leak the two-slot concurrency semaphore.
        # Streaming transfers ownership to the iterator below instead.
        if not stream_handoff:
            if response is not None:
                await response.aclose()
            await client.aclose()
            _INFLIGHT.release()

    async def chunks():
        total = 0
        try:
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > _MAX_RESPONSE:
                    raise RuntimeError("relay_response_limit_reached")
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()
            _INFLIGHT.release()

    return StreamingResponse(
        chunks(), media_type="text/event-stream" if streaming else "application/json",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
