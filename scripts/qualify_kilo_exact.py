#!/usr/bin/env python3
"""Qualify one or more exact zero-cost Kilo models with a tiny tool canary.

No retries. No router aliases. No raw prompts or secrets are persisted.
Results append to a private JSONL ledger for the read-only free-pool projection.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

KILO_MODELS_URL = "https://api.kilo.ai/api/openrouter/models"
KILO_CHAT_URL = "https://api.kilo.ai/api/openrouter/chat/completions"
ANONYMOUS_BEARER = "anonymous"
DEFAULT_STATE_ROOT = (
    pathlib.Path.home() / ".local" / "state" / "auto-assist" / "subagents"
)
ROUTER_ALIAS_TOKENS = frozenset({"free", "auto", "router", "any", "best", "default"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_exact(model_id: str) -> bool:
    leaf = str(model_id or "").rsplit("/", 1)[-1].split(":", 1)[0].lower()
    return bool(model_id) and leaf not in ROUTER_ALIAS_TOKENS


def _zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _request_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {ANONYMOUS_BEARER}",
        "User-Agent": "auto-assist-free-qualification/1.0",
    }
    data = None
    method = "GET"
    if payload is not None:
        method = "POST"
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
        headers["X-KILOCODE-EDITORNAME"] = "OmniRoute"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode(errors="replace"))
        except Exception:
            body = {}
        return error.code, body


def fetch_catalog(timeout: float = 20.0) -> dict[str, dict[str, Any]]:
    status, payload = _request_json(KILO_MODELS_URL, timeout=timeout)
    if status != 200:
        raise RuntimeError(f"Kilo catalog returned HTTP {status}")
    models: dict[str, dict[str, Any]] = {}
    for record in payload.get("data", []):
        if not isinstance(record, dict):
            continue
        model_id = str(record.get("id") or "")
        pricing = record.get("pricing") or {}
        if not _is_exact(model_id):
            continue
        if not (_zero(pricing.get("prompt")) and _zero(pricing.get("completion"))):
            continue
        models[model_id] = record
    return models


def validate_candidate(model_id: str, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not _is_exact(model_id):
        raise ValueError(f"router alias is not qualifiable: {model_id!r}")
    record = catalog.get(model_id)
    if record is None:
        raise ValueError(f"model is not live exact zero-cost catalog entry: {model_id!r}")
    params = set(record.get("supported_parameters") or [])
    if "tools" not in params:
        raise ValueError(f"model does not advertise tool support: {model_id!r}")
    return record


def _failure_state(status: int, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    mapping = {
        429: "rate_limited",
        503: "provider_overloaded",
        402: "payment_required",
        401: "auth_error",
        403: "auth_error",
    }
    state = mapping.get(status, "provider_error")
    metadata: dict[str, Any] = {}
    error = body.get("error")
    if isinstance(error, dict):
        meta = error.get("metadata")
        if isinstance(meta, dict):
            for key in ("provider_name", "limit_source", "is_byok"):
                if key in meta:
                    metadata[key] = meta[key]
        code = error.get("code")
        if code is not None:
            metadata["provider_code"] = code
    return state, metadata


def evaluate_success(
    model_id: str,
    status: int,
    response: dict[str, Any],
) -> dict[str, Any]:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    tool_calls = message.get("tool_calls") or []
    response_model = str(response.get("model") or "")
    usage = response.get("usage") or {}
    cost_details = usage.get("cost_details") or {}

    names: list[str] = []
    arguments: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        if isinstance(function, dict):
            names.append(str(function.get("name") or ""))
            arguments.append(str(function.get("arguments") or ""))

    structured = bool(tool_calls and "echo_value" in names)
    model_match = response_model == model_id
    try:
        cost = float(usage.get("cost") or 0.0)
    except (TypeError, ValueError):
        cost = -1.0
    upstream_value = cost_details.get("upstream_inference_cost")
    try:
        upstream_cost = (
            float(upstream_value) if upstream_value is not None else None
        )
    except (TypeError, ValueError):
        upstream_cost = -1.0

    qualified = bool(
        status == 200
        and model_match
        and structured
        and cost == 0.0
        and (upstream_cost is None or upstream_cost == 0.0)
    )
    return {
        "response_model": response_model or None,
        "model_identity_complete": model_match,
        "structured_tool_call": structured,
        "tool_names": names,
        "tool_arguments": arguments,
        "finish_reason": choice.get("finish_reason"),
        "tokens": usage,
        "cost": cost,
        "upstream_inference_cost": upstream_cost,
        "qualified": qualified,
        "capacity_state": "usable" if qualified else "unqualified",
    }


def qualify_model(
    model_id: str,
    *,
    catalog: dict[str, dict[str, Any]],
    timeout: float = 40.0,
) -> dict[str, Any]:
    catalog_record = validate_candidate(model_id, catalog)
    started = _now()
    start_clock = time.monotonic()
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": "Call echo_value exactly once with value QUALIFIED_OK. Do not answer normally.",
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "echo_value",
                    "description": "Return a supplied value.",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                },
            }
        ],
        "tool_choice": "auto",
        "max_tokens": 160,
        "temperature": 0,
    }
    status, response = _request_json(
        KILO_CHAT_URL,
        payload=payload,
        timeout=timeout,
    )
    elapsed = round(time.monotonic() - start_clock, 3)

    result: dict[str, Any] = {
        "schema": "free-provider-qualification/v1",
        "provider": "kilo_free",
        "requested_model": model_id,
        "started_at": started,
        "elapsed_s": elapsed,
        "http_status": status,
        "context_length": catalog_record.get("context_length"),
        "supported_parameters": catalog_record.get("supported_parameters") or [],
    }
    if status == 200:
        result.update(evaluate_success(model_id, status, response))
    else:
        state, metadata = _failure_state(status, response)
        result.update(
            {
                "qualified": False,
                "model_identity_complete": False,
                "structured_tool_call": False,
                "response_model": None,
                "cost": 0,
                "upstream_inference_cost": None,
                "capacity_state": state,
                **metadata,
            }
        )
    return result


@contextlib.contextmanager
def _ledger_lock(ledger: pathlib.Path):
    ledger.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ledger.parent, 0o700)
    lock = ledger.with_name(ledger.name + ".lock")
    with lock.open("a+", encoding="utf-8") as handle:
        os.chmod(lock, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_record(ledger: pathlib.Path, record: dict[str, Any]) -> None:
    with _ledger_lock(ledger):
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    record,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(ledger, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--state-root", type=pathlib.Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--timeout", type=float, default=40.0)
    args = parser.parse_args(argv)

    catalog = fetch_catalog()
    ledger = args.state_root / "provider-qualification.jsonl"
    failed = False
    for model_id in args.model:
        try:
            record = qualify_model(model_id, catalog=catalog, timeout=args.timeout)
        except Exception as error:
            record = {
                "schema": "free-provider-qualification/v1",
                "provider": "kilo_free",
                "requested_model": model_id,
                "started_at": _now(),
                "qualified": False,
                "capacity_state": "qualification_error",
                "error_type": type(error).__name__,
            }
        append_record(ledger, record)
        print(json.dumps(record, sort_keys=True))
        failed = failed or not bool(record.get("qualified"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
