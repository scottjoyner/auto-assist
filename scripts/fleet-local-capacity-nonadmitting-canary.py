#!/usr/bin/env python3
"""Opt-in, local-only, bounded LM Studio inference capacity witness.

NEVER reads Task graph, signs an approval, starts/stops a model, or assigns slots.
Even a successful canary exits 2/HOLD: human approved admission is separate.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
import urllib.error
import urllib.request
from typing import Any

ORIGIN = "http://127.0.0.1:1234"
CATALOG = ORIGIN + "/api/v0/models"
GENERATE = ORIGIN + "/v1/chat/completions"
MAX_TOKENS = 64
MAX_RESPONSE_BYTES = 65536
# Only static, public error categories are emitted. Never expose arbitrary
# exception messages containing private model URLs, prompts or credentials.
_SAFE_FAILURE_CATEGORIES = frozenset(
    {
        "loaded_model_identity_mismatch",
        "nonproductive_completion",
        "served_model_identity_mismatch",
        "completion_budget_unverified",
        "completion_shape_unverified",
        "response_exceeds_limit",
        "unexpected_response_shape",
        "catalog_not_list",
        "model_identity_invalid",
        "origin_not_loopback_pinned",
    }
)


def _safe_failure_reason(error: Exception) -> str:
    value = str(error) if isinstance(error, ValueError) else ""
    return value.upper() if value in _SAFE_FAILURE_CATEGORIES else "LOCAL_CAPACITY_EVIDENCE_INCOMPLETE"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def _opener() -> Any:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _json_response(client: Any, req: urllib.request.Request, timeout: int) -> dict:
    if req.full_url not in {CATALOG, GENERATE}:
        raise ValueError("origin_not_loopback_pinned")
    with client.open(req, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("local_response_not_200")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("response_exceeds_limit")
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("unexpected_response_shape")
    return decoded


def _loaded_model(client: Any, expected_id: str) -> None:
    request = urllib.request.Request(CATALOG, method="GET")
    obj = _json_response(client, request, 4)
    rows = obj.get("data")
    if not isinstance(rows, list):
        raise ValueError("catalog_not_list")
    loaded = [r.get("id") for r in rows if isinstance(r, dict) and r.get("state") == "loaded"]
    if loaded != [expected_id]:
        raise ValueError("loaded_model_identity_mismatch")


def _once(model_id: str, request_number: int, client: Any) -> dict[str, Any]:
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply OK."}],
        "temperature": 0,
        "stream": False,
        "max_tokens": MAX_TOKENS,
    }
    req = urllib.request.Request(
        GENERATE,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    start = time.monotonic()
    value = _json_response(client, req, 16)
    usage = value.get("usage")
    choices = value.get("choices")
    if not isinstance(usage, dict) or not isinstance(choices, list) or not choices:
        raise ValueError("completion_shape_unverified")
    # A reported token count is not proof of a usable generation. Require
    # real non-blank text and a strictly positive bounded count; a no-content
    # or wrong-model response cannot become capacity admission evidence.
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("nonproductive_completion")
    returned_model = value.get("model")
    if returned_model is not None and returned_model != model_id:
        raise ValueError("served_model_identity_mismatch")
    count = usage.get("completion_tokens")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > MAX_TOKENS:
        raise ValueError("completion_budget_unverified")
    return {
        "request": request_number,
        "elapsed_ms": round((time.monotonic() - start) * 1000),
        "completion_tokens": count,
        "http": 200,
    }


def run_canary(model_id: str, *, client_factory: Any = _opener) -> dict[str, Any]:
    if not model_id or len(model_id) > 160 or any(x in model_id for x in "\r\n\x00"):
        raise ValueError("model_identity_invalid")
    _loaded_model(client_factory(), model_id)
    solo = _once(model_id, 0, client_factory())
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(_once, model_id, n, client_factory()) for n in (1, 2)]
        pair = [f.result(timeout=18) for f in pending]
    _loaded_model(client_factory(), model_id)  # no silent model switch
    results = [solo, *pair]
    return {
        "model_id": model_id,
        "requests": results,
        # Two client calls were dispatched through the pool. No server
        # slot-level trace or actual overlap was independently witnessed.
        "client_parallel_requests_completed": True,
        "physical_parallel_pair_observed": False,
        "server_parallel_processing_verified": False,
        "completion_token_budget_per_request": MAX_TOKENS,
        "approved_parallel_slots": 0,
        "rollback_verified": False,
        "owner_signed_projection_verified": False,
        "task_admission": "HOLD",
        "status": "HOLD",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Opt in to three bounded local synthetic generations")
    parser.add_argument("--model-id", default="ornith-1.5-35b-a3b-apex-mtp")
    args = parser.parse_args(argv)
    if not args.live:
        print(
            json.dumps(
                {"status": "HOLD", "task_admission": "HOLD", "reason": "LIVE_LOCAL_CANARY_NOT_REQUESTED"},
                sort_keys=True,
            )
        )
        return 2
    try:
        result = run_canary(args.model_id)
    except (
        OSError,
        ValueError,
        TimeoutError,
        urllib.error.URLError,
        concurrent.futures.TimeoutError,
        KeyError,
        TypeError,
    ) as exc:
        result = {
            "status": "HOLD",
            "task_admission": "HOLD",
            "reason": _safe_failure_reason(exc),
            "approved_parallel_slots": 0,
        }
    print(json.dumps(result, sort_keys=True))
    return 2  # Success is observation, never approval.


if __name__ == "__main__":
    raise SystemExit(main())
