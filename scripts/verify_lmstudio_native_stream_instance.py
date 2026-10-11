"""Offline LM Studio native chat SSE evidence: model *instance*, not worker PID.

No network, model calls, disk I/O, secret access, signing or dispatcher.
Consumes caller-supplied operator-approved SYNTHETIC / sanitized SSE bytes
only; rejects raw reasoning/tools, errors, lazy model loads and incomplete
streams. The returned digest is NOT a worker execution or owner receipt.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

MAX_STREAM_BYTES = 131_072
MAX_EVENT_COUNT = 256
MAX_CONTENT_BYTES = 8192
INSTANCE = re.compile(r"[A-Za-z0-9_.:/@+-]{3,128}\Z")
RESPONSE = re.compile(r"resp_[A-Za-z0-9_-]{8,128}\Z")
ACCEPTED = {
    "chat.start", "prompt_processing.start", "prompt_processing.progress",
    "prompt_processing.end", "message.start", "message.delta",
    "message.end", "chat.end",
}


@dataclass(frozen=True)
class StreamEvidence:
    model_instance_id: str
    response_id: str
    output_sha256: str
    output_tokens: int
    stream_sha256: str
    event_count: int


def _dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(label + " must be an object")
    return value


def parse(
    data: bytes, *, expected_loaded_instance_id: str,
    max_output_tokens: int = 24,
) -> StreamEvidence:
    """Verify an ALREADY-CAPTURED benign streaming response. Never call API."""
    if type(data) is not bytes or not 0 < len(data) <= MAX_STREAM_BYTES:
        raise ValueError("stream missing or exceeds privacy/size budget")
    if not isinstance(expected_loaded_instance_id, str) or not INSTANCE.fullmatch(
        expected_loaded_instance_id
    ):
        raise ValueError("expected independently selected instance invalid")
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 128:
        raise ValueError("invalid admitted canary output-token budget")
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid UTF-8 SSE") from exc
    if "\r" in raw:
        raw = raw.replace("\r\n", "\n")
        if "\r" in raw:
            raise ValueError("unrecognized SSE line ending")
    if not raw.endswith("\n\n"):
        raise ValueError("incomplete SSE stream without final event boundary")
    events = raw.split("\n\n")[:-1]
    if not events or len(events) > MAX_EVENT_COUNT:
        raise ValueError("event budget invalid")

    phase = "before_start"
    prompt_processing_seen = False
    model_id: str | None = None
    response_id: str | None = None
    content_parts: list[str] = []
    content_bytes = 0
    output_hash: str | None = None
    total_tokens = 0
    for block in events:
        lines = block.splitlines()
        if not lines or len(lines) != 2:
            raise ValueError("one event and one JSON data line required")
        if not lines[0].startswith("event: ") or not lines[1].startswith("data: "):
            raise ValueError("unsupported SSE shape")
        name = lines[0][len("event: "):]
        if name not in ACCEPTED:
            # This includes model_load.start/progress/end, SSE 'error',
            # tool calls, reasoning and any unknown future event types.
            raise ValueError("unadmitted event: " + name[:32])
        try:
            payload = _dict(json.loads(lines[1][len("data: "):]), "SSE event")
        except json.JSONDecodeError as exc:
            raise ValueError("malformed SSE event") from exc
        if payload.get("type") != name:
            raise ValueError("SSE event name/type mismatch")

        if name == "chat.start":
            if phase != "before_start" or set(payload) != {"type", "model_instance_id"}:
                raise ValueError("unexpected or duplicated chat.start")
            model_id = payload["model_instance_id"]
            if not isinstance(model_id, str) or model_id != expected_loaded_instance_id:
                raise ValueError("loaded model instance ID mismatch")
            phase = "started"
        elif name == "prompt_processing.start":
            if (phase != "started" or prompt_processing_seen
                    or set(payload) != {"type"}):
                raise ValueError("unexpected or repeated prompt_processing.start")
            prompt_processing_seen = True
            phase = "prompt_processing"
        elif name == "prompt_processing.progress":
            if phase != "prompt_processing" or set(payload) != {"type", "progress"}:
                raise ValueError("unexpected prompt_processing.progress")
            progress = payload["progress"]
            if (type(progress) not in (int, float)
                    or not math.isfinite(progress) or not 0 <= progress <= 1):
                raise ValueError("invalid bounded prompt_processing.progress")
        elif name == "prompt_processing.end":
            if phase != "prompt_processing" or set(payload) != {"type"}:
                raise ValueError("unexpected prompt_processing.end")
            phase = "started"
        elif name == "message.start":
            if phase != "started" or set(payload) != {"type"}:
                raise ValueError("unexpected message.start")
            phase = "message"
        elif name == "message.delta":
            if phase != "message" or set(payload) != {"type", "content"}:
                raise ValueError("unexpected content delta")
            text = payload["content"]
            if not isinstance(text, str):
                raise ValueError("content delta is not text")
            content_bytes += len(text.encode("utf-8"))
            if content_bytes > MAX_CONTENT_BYTES:
                raise ValueError("streaming content exceeds privacy budget")
            content_parts.append(text)
        elif name == "message.end":
            if phase != "message" or set(payload) != {"type"}:
                raise ValueError("unexpected message.end")
            if not content_parts or not "".join(content_parts):
                raise ValueError("empty completion is not an inference receipt")
            phase = "message_done"
        elif name == "chat.end":
            if phase != "message_done" or set(payload) != {"type", "result"}:
                raise ValueError("missing message completion before chat.end")
            result = _dict(payload["result"], "chat.end result")
            if not {"model_instance_id", "output", "stats", "response_id"}.issubset(
                result
            ):
                raise ValueError("chat.end missing mandatory fields")
            if result["model_instance_id"] != model_id:
                raise ValueError("chat.end model instance differs from start")
            response_id = result["response_id"]
            if not isinstance(response_id, str) or not RESPONSE.fullmatch(response_id):
                raise ValueError("missing stored response_id")
            output = result["output"]
            if (not isinstance(output, list) or len(output) != 1
                    or not isinstance(output[0], dict)
                    or set(output[0]) != {"type", "content"}
                    or output[0]["type"] != "message"):
                raise ValueError("unexpected output shape or tool/reasoning content")
            final_message = output[0]["content"]
            if not isinstance(final_message, str) or final_message != "".join(
                content_parts
            ):
                raise ValueError("streamed and finalized response differ")
            stats = _dict(result["stats"], "chat.end stats")
            total_tokens = stats.get("total_output_tokens")
            if type(total_tokens) is not int or not (
                1 <= total_tokens <= max_output_tokens
            ):
                raise ValueError("canary output token budget exceeded or unknown")
            output_hash = hashlib.sha256(final_message.encode("utf-8")).hexdigest()
            phase = "complete"

        if phase == "complete" and block != events[-1]:
            raise ValueError("extra event after completed chat")

    if phase != "complete" or model_id is None or response_id is None or (
        output_hash is None
    ):
        raise ValueError("stream ended without terminal chat.end")
    return StreamEvidence(
        model_instance_id=model_id,
        response_id=response_id,
        output_sha256=output_hash,
        output_tokens=total_tokens,
        stream_sha256=hashlib.sha256(data).hexdigest(),
        event_count=len(events),
    )


def review_only(evidence: StreamEvidence) -> dict:
    """No trusted source, worker process ID or signed owner binding exists."""
    return {
        "schema": "lmstudio-native-model-instance-stream-review-v1",
        "status": "GATEWAY_MODEL_INSTANCE_ONLY",
        "model_instance_id": evidence.model_instance_id,
        "response_id": evidence.response_id,
        "output_sha256": evidence.output_sha256,
        "output_tokens": evidence.output_tokens,
        "stream_sha256": evidence.stream_sha256,
        "event_count": evidence.event_count,
        "unexpected_model_load_observed": False,
        "gateway_source_authenticated": False,
        "physical_worker_pid_attested": False,
        "worker_execution_attested": False,
        "owner_bound_request_attested": False,
        "can_sign_physical_source": False,
        "can_approve": False,
        "can_dispatch": False,
    }
