#!/usr/bin/env python3
"""Non-admitting host-loopback LFM3 server-side slot overlap witness.

Opt-in only. 2 short synthetic generation requests to an ALREADY RUNNING local
LFM3 process while GET /slots independently samples active server workers.
Never loads a model, forwards traffic, reads signer/Task graph or grants slots.
Even independent physical overlap returns exit 2/HOLD for Task admission.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

ORIGIN = "http://127.0.0.1:19180"
MODEL = "lfm3-local-test"
SLOT_COUNT = 4
MAX_OUTPUT_TOKENS = 256
MAX_RESPONSE_BYTES = 128 * 1024
MAX_SAMPLES = 300
SAMPLE_INTERVAL_SEC = 0.04


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _client():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def _http_json(route: str, *, body: bytes | None = None, opener: Any | None = None) -> Any:
    if route not in ("/health", "/v1/models", "/slots", "/v1/chat/completions"):
        raise ValueError("not_a_pinned_local_route")
    if (route == "/v1/chat/completions") != (body is not None):
        raise ValueError("wrong_upstream_method")
    if body is not None and len(body) > 1024:
        raise ValueError("oversized_canary_request")
    req = urllib.request.Request(
        ORIGIN + route,
        method="POST" if body is not None else "GET",
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with (opener or _client()).open(req, timeout=22 if body is not None else 3) as response:
        if response.status != 200:
            raise ValueError("upstream_not_healthy")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("oversized_upstream_result")
    return json.loads(raw)


def _parse_busy_slots(payload: Any) -> int:
    if (
        not isinstance(payload, list)
        or len(payload) != SLOT_COUNT
        or any(not isinstance(x, dict) or type(x.get("is_processing")) is not bool for x in payload)
    ):
        raise ValueError("unverified_server_slots")
    return sum(x["is_processing"] is True for x in payload)


def _slots() -> int:
    return _parse_busy_slots(_http_json("/slots"))


def _ready() -> None:
    health = _http_json("/health")
    models = _http_json("/v1/models")
    if (
        not isinstance(health, dict)
        or health.get("status") not in ("ok", "healthy")
        or not isinstance(models, dict)
        or not isinstance(models.get("data"), list)
        or len(models["data"]) != 1
        or not isinstance(models["data"][0], dict)
        or models["data"][0].get("id") != MODEL
    ):
        raise ValueError("incorrect_model_residency")
    if _slots() != 0:
        raise ValueError("live_slots_already_busy")


def _parse_completion(payload: Any, number: int, duration_ms: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("invalid_completion_shape")
    if payload.get("model") is not None and payload["model"] != MODEL:
        raise ValueError("incorrect_served_model")
    choices = payload.get("choices")
    usage = payload.get("usage")
    if (
        not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], dict)
        or not isinstance(usage, dict)
    ):
        raise ValueError("invalid_completion_shape")
    answer = choices[0].get("message")
    answer = answer.get("content") if isinstance(answer, dict) else None
    count = usage.get("completion_tokens")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("empty_final_completion")
    if type(count) is not int or not 1 <= count <= MAX_OUTPUT_TOKENS:
        raise ValueError("unverified_completion_budget")
    return {
        "request": number,
        "http": 200,
        "completion_tokens": count,
        "duration_ms": duration_ms,
        "final_content_nonblank": True,
    }


def _generate(number: int, barrier: threading.Barrier) -> dict[str, Any]:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Reply OK."}],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "stream": False,
    }
    data = json.dumps(payload, separators=(",", ":")).encode()
    barrier.wait(timeout=4)
    started = time.monotonic()
    output = _http_json("/v1/chat/completions", body=data)
    return _parse_completion(output, number, round((time.monotonic() - started) * 1000))


def interpret_observations(results: list[dict[str, Any]], busy_samples: list[int], after_busy: int) -> dict[str, Any]:
    if (
        len(results) != 2
        or not busy_samples
        or any(type(n) is not int or not 0 <= n <= SLOT_COUNT for n in busy_samples)
        or type(after_busy) is not int
        or not 0 <= after_busy <= SLOT_COUNT
    ):
        raise ValueError("incomplete_slot_trace")
    if any(
        x.get("http") != 200
        or x.get("final_content_nonblank") is not True
        or type(x.get("completion_tokens")) is not int
        or not 1 <= x["completion_tokens"] <= MAX_OUTPUT_TOKENS
        for x in results
    ):
        raise ValueError("incomplete_generation_evidence")
    peak = max(busy_samples)
    overlap = peak >= 2 and after_busy == 0
    return {
        "contract": "lfm3-server-slot-overlap-nonadmitting-v1",
        "model": MODEL,
        "configured_slots": SLOT_COUNT,
        "slot_samples": len(busy_samples),
        "peak_simultaneously_busy_server_slots": peak,
        "independently_observed_server_overlap": overlap,
        "slot_ids_idle_after": after_busy == 0,
        "request_results": results,
        "physical_evidence": "PASS_TWO_SERVER_SLOTS" if overlap else "HOLD_OVERLAP_UNVERIFIED",
        "short_canary_only": True,
        "sustained_throughput_slo_verified": False,
        "owner_signed_projection_verified": False,
        "approved_task_slots": 0,
        "task_admission": "HOLD",
        "status": "HOLD",
    }


def run_live() -> dict[str, Any]:
    _ready()
    barrier = threading.Barrier(3)
    samples: list[int] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_generate, i, barrier) for i in (1, 2)]
        barrier.wait(timeout=4)
        began = time.monotonic()
        while any(not f.done() for f in futures) and len(samples) < MAX_SAMPLES:
            if time.monotonic() - began > 18:
                break
            samples.append(_slots())
            time.sleep(SAMPLE_INTERVAL_SEC)
        results = [f.result(timeout=25) for f in futures]
    return interpret_observations(results, samples, _slots())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly opt in to two bounded already-loaded LFM3 generations"
    )
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
        output = run_live()
    except (
        OSError,
        ValueError,
        TimeoutError,
        KeyError,
        TypeError,
        urllib.error.URLError,
        concurrent.futures.TimeoutError,
        threading.BrokenBarrierError,
    ):
        output = {
            "status": "HOLD",
            "task_admission": "HOLD",
            "reason": "SERVER_SLOT_EVIDENCE_INCOMPLETE",
            "approved_task_slots": 0,
        }
    print(json.dumps(output, sort_keys=True))
    return 2  # Any physical PASS must remain non-admitting.


if __name__ == "__main__":
    raise SystemExit(main())
