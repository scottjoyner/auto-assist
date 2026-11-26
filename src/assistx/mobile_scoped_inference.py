"""Candidate mobile inference lane: real fenced claims, no shared chat storage.

This deliberately does not launch Hermes or grant native tools. Server-owned
read-only evidence adapters remain separate from model execution.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from .executor_claims import assert_claim_matches, read_claim_state
from .executor_security import ExecutorTokenCodec

PROFILE = "kipnerter-agent-v1"
MODEL = "auto/local"
AGENT = "kipnerter-mobile-scoped-inference"
MAX_INPUT_CHARS = 24000
MAX_OUTPUT = 1024
LEASE_SECONDS = 120
MAX_TIMEOUT = 75
_TASK_PAYLOAD = {"model": MODEL, "queue_class": "interactive", "profile": PROFILE}


class MobileInferenceError(RuntimeError):
    pass


def make_claims(state: dict[str, Any], *, task_id: str, claim_id: str,
                generation: int, now: int) -> dict[str, Any]:
    expiry = min(int(state.get("lease_expires_at_ts") or 0) // 1000,
                 int(state.get("projection_expires_at_ts") or 0) // 1000,
                 now + MAX_TIMEOUT + 10)
    if expiry <= now + 10:
        raise MobileInferenceError("executor_claim_unavailable")
    claims = {
        "iss": "assistx", "aud": ["auto-router"], "iat": now, "nbf": now,
        "exp": expiry, "jti": uuid.uuid4().hex,
        "task_id": task_id, "claim_id": claim_id, "agent_id": AGENT,
        "projection_generation": generation, "scopes": ["inference"],
        "allowed_model_aliases": [MODEL], "allowed_tools": [],
        "repository": "", "allowed_paths": [],
        "max_input_tokens": 32768, "max_output_tokens": MAX_OUTPUT,
        "max_attempts": 1,
    }
    assert_claim_matches(claims, state)
    return claims


def make_payload(messages: list[dict[str, str]], claims: dict[str, Any]) -> dict[str, Any]:
    # Client-supplied system/tool messages are not trusted tool observations.
    if not messages or messages[-1].get("role") != "user":
        raise MobileInferenceError("invalid_mobile_messages")
    bounded = []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role not in {"user", "assistant", "system"} or not isinstance(content, str):
            raise MobileInferenceError("invalid_mobile_messages")
        if role == "system":
            role, content = "user", "Untrusted device context:\n" + content
        bounded.append({"role": role, "content": content})
    if sum(len(m["content"]) for m in bounded) > MAX_INPUT_CHARS:
        raise MobileInferenceError("mobile_context_too_large")
    identity = {k: claims[k] for k in
                ("task_id", "claim_id", "agent_id", "projection_generation")}
    return {
        "model": MODEL,
        "messages": [{"role": "system", "content":
            "Answer concisely. Local device observations do not establish server "
            "connectivity, ingestion, or fleet health. No fleet tools are available "
            "in this inference-only lane; report unavailable evidence as unknown. "
            "Return only the final answer."}, *bounded],
        "temperature": 0.2, "max_tokens": MAX_OUTPUT, "stream": False,
        "local_only": True, "allow_cloud": False,
        "metadata": {"assistx_executor": identity, "privacy": "local_only",
                     "local_only": True, "queue_class": "interactive",
                     "assistx_source": True},
    }


def final_content(response: dict[str, Any]) -> str:
    try:
        choice = response["choices"][0]
        message = choice["message"]
        content = message.get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise MobileInferenceError("invalid_inference_response") from None
    if (not isinstance(content, str) or message.get("tool_calls")
            or choice.get("finish_reason") not in {"stop", None}):
        raise MobileInferenceError("invalid_inference_response")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I).strip()
    if not content or re.search(r"</?think\b", content, re.I):
        raise MobileInferenceError("invalid_inference_response")
    return content


def _open_claim(neo_factory: Callable[[], Any], admission: Callable[[Any], bool],
                generation: int, signer: Callable[[dict], str]) -> tuple[str, str, dict, str]:
    neo = neo_factory()
    task_id = claim_id = ""
    try:
        if not admission(neo):
            raise MobileInferenceError("mobile_admission_unavailable")
        # Never persist message text, user identifiers, or model output in shared tasks.
        task_id = neo.upsert_ticket(title="Mobile scoped inference", ticket_type="mobile_inference",
                    kind="mobile_inference", status="READY", required_capabilities=["mobile_inference"],
                    target_agent_id=AGENT, priority="HIGH", payload=dict(_TASK_PAYLOAD), max_migrations=0)
        result = neo.claim_task(task_id, AGENT, capabilities=["mobile_inference"],
                                session_id=uuid.uuid4().hex, lease_seconds=LEASE_SECONDS)
        if not result.get("claimed"):
            raise MobileInferenceError("mobile_claim_failed")
        claim_id = str((result.get("task") or {}).get("claim_id") or result.get("claim_id") or "")
        if not claim_id:
            raise MobileInferenceError("mobile_claim_failed")
        state = read_claim_state(neo_factory, task_id)
        claims = make_claims(state, task_id=task_id, claim_id=claim_id,
                             generation=generation, now=int(time.time()))
        token = signer(claims)
        if not token:
            raise MobileInferenceError("executor_authentication_failed")
        return task_id, claim_id, claims, token
    except BaseException:
        if task_id:
            # Only our exact claim can be completed. A READY task is targeted
            # to this dedicated executor and contains no private request data.
            if claim_id:
                neo.complete_task(task_id, AGENT, "FAILED", result={"profile": PROFILE},
                                  claim_id=claim_id)
        raise
    finally:
        neo.close()


def _finish(neo_factory: Callable[[], Any], task_id: str, claim_id: str, status: str) -> bool:
    neo = neo_factory()
    try:
        # No summary: the generic completion code creates shared memory when
        # a summary is supplied. The receipt contains no prompt or answer.
        return bool(neo.complete_task(task_id, AGENT, status,
                     result={"profile": PROFILE, "status": status}, claim_id=claim_id))
    finally:
        neo.close()


async def execute(messages: list[dict[str, str]], *,
                  neo_factory: Callable[[], Any], admission: Callable[[Any], bool],
                  projection: dict[str, Any], router_url: str,
                  transport: httpx.AsyncBaseTransport | None = None,
                  signer: Callable[[dict], str] | None = None) -> str:
    now = int(time.time())
    generation = int(projection.get("generation") or 0)
    if generation <= 0 or int(projection.get("expires_at_ms") or 0) <= (now + 10) * 1000:
        raise MobileInferenceError("runtime_projection_unavailable")
    url = urlsplit(router_url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise MobileInferenceError("mobile_router_unavailable")
    # Validate client messages before creating a task.
    make_payload(messages, {"task_id": "", "claim_id": "", "agent_id": AGENT,
                            "projection_generation": generation})
    if signer is None:
        signer = ExecutorTokenCodec.signer_from_env().encode
    opening = asyncio.create_task(asyncio.to_thread(_open_claim, neo_factory, admission, generation, signer))
    try:
        task_id, claim_id, claims, token = await asyncio.shield(opening)
    except asyncio.CancelledError:
        # A thread may finish claiming after cancellation; collect its result
        # and fence it before propagating cancellation.
        try:
            task_id, claim_id, _, _ = await opening
            await asyncio.to_thread(_finish, neo_factory, task_id, claim_id, "CANCELLED")
        except Exception:
            pass
        raise
    status = "FAILED"
    try:
        timeout = min(MAX_TIMEOUT, claims["exp"] - time.time() - 2)
        if timeout <= 0:
            raise MobileInferenceError("executor_claim_unavailable")
        async with httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False, trust_env=False) as client:
            async with asyncio.timeout(timeout):
                response = await client.post(router_url.rstrip("/") + "/v1/chat/completions",
                    json=make_payload(messages, claims), headers={"Authorization": "Bearer " + token})
        if response.status_code in (401, 403):
            raise MobileInferenceError("executor_authentication_failed")
        if response.status_code != 200:
            raise MobileInferenceError("mobile_inference_unavailable")
        output = final_content(response.json())
        state = await asyncio.to_thread(read_claim_state, neo_factory, task_id)
        assert_claim_matches(claims, state)
        if claims["exp"] <= time.time():
            raise MobileInferenceError("executor_claim_unavailable")
        status = "DONE"
    except asyncio.CancelledError:
        status = "CANCELLED"
        raise
    finally:
        finished = await asyncio.shield(asyncio.to_thread(_finish, neo_factory, task_id, claim_id, status))
        if status == "DONE" and not finished:
            raise MobileInferenceError("executor_completion_fenced")
    return output
