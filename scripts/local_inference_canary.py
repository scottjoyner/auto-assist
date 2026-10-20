#!/usr/bin/env python3
"""One bounded, metadata-only llama.cpp canary; never loads or changes a model."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

PROMPT = "Reply with exactly READY."
MAX_RESPONSE_BYTES = 262144
ALLOWED_PORTS = {1235, 1236, 8399}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def endpoint_url(raw: str, allow_private: bool) -> str:
    url = urllib.parse.urlsplit(raw)
    if (url.scheme != "http" or url.username or url.password or url.query
            or url.fragment or url.path not in ("", "/")):
        raise ValueError("endpoint must be a clean private HTTP origin")
    try:
        address = ipaddress.ip_address(url.hostname or "")
        port = url.port
    except (ValueError, TypeError) as exc:
        raise ValueError("endpoint must use a literal IP and valid port") from exc
    if port not in ALLOWED_PORTS:
        raise ValueError("model endpoint port not in approved canary allowlist")
    if not address.is_loopback and not (allow_private and address.is_private):
        raise ValueError("remote private IP requires explicit --allow-private")
    return raw.rstrip("/")


def request_json(origin: str, path: str, payload=None, timeout=20):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(origin + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=timeout) as response:
        status = response.status
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("backend response exceeded bound")
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError("backend response was not a JSON object")
    return status, parsed



class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def stream_chat(origin: str, payload: dict, timeout: int):
    """Consume SSE into memory; only aggregates and hashes can leave this function."""
    body = dict(payload)
    body["stream"] = True
    body["stream_options"] = {"include_usage": True}
    req = urllib.request.Request(
        origin + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    chunks = []
    received = 0
    first_content_ms = None
    usage = None
    timing = {}
    done = False
    finish_reason = None
    started = time.monotonic()
    with _OPENER.open(req, timeout=timeout) as response:
        for line in response:
            received += len(line)
            if received > MAX_RESPONSE_BYTES or len(line) > 16384:
                raise ValueError("stream exceeds bounded receipt input")
            if not line.startswith(b"data:"):
                continue
            value = line[5:].strip()
            if value == b"[DONE]":
                done = True
                break
            event = json.loads(value)
            if not isinstance(event, dict):
                raise ValueError("invalid SSE event")
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            if isinstance(event.get("timings"), dict):
                timing = event["timings"]
            choices = event.get("choices")
            item = choices[0] if isinstance(choices, list) and choices else {}
            item = item if isinstance(item, dict) else {}
            if isinstance(item.get("finish_reason"), str):
                finish_reason = item["finish_reason"]
            delta = item.get("delta")
            delta = delta if isinstance(delta, dict) else {}
            fragment = delta.get("content")
            if isinstance(fragment, str) and fragment:
                if first_content_ms is None:
                    first_content_ms = round((time.monotonic() - started) * 1000, 2)
                chunks.append(fragment)
            if sum(map(len, chunks)) > 8192:
                raise ValueError("stream content bound exceeded")
    if not done:
        raise ValueError("stream ended without DONE")
    return {"choices": [{"message": {"content": "".join(chunks)},
                         "finish_reason": finish_reason}],
            "usage": usage, "timings": timing,
            "first_content_ms": first_content_ms, "stream_done": done}


def safe_int(obj, key):
    val = obj.get(key)
    return val if type(val) is int and val >= 0 else None


def run(args):
    origin = endpoint_url(args.endpoint, args.allow_private)
    if not 1 <= args.n_predict <= 32:
        raise ValueError("n_predict must be between 1 and 32")
    receipt = {
        "schema": "assistx-local-inference-canary/v1",
        "node_claim": args.node,
        "producer": "manual-readonly-canary",
        "trace_id": uuid.uuid4().hex,
        "request_id": str(uuid.uuid4()),
        "started_utc": utc_now(),
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "requested_output_limit": args.n_predict,
        "requested_model_sha256": hashlib.sha256(args.model.encode()).hexdigest(),
        "route": "direct-existing-llama-cpp",
        "gateway_proven": False,
        "result": "unknown",
    }
    start = time.monotonic()
    try:
        _, health = request_json(origin, "/health", timeout=4)
        if health.get("status") != "ok":
            raise ValueError("non-ready backend (no model load attempted)")
        _, props = request_json(origin, "/props", timeout=4)
        receipt["health_qualified"] = True
        receipt["props_sha256"] = hashlib.sha256(
            json.dumps(props, sort_keys=True).encode()).hexdigest()
        req = {"model": args.model, "messages": [{"role": "user", "content": PROMPT}],
               "max_tokens": args.n_predict, "stream": False, "temperature": 0}
        if args.stream:
            body = stream_chat(origin, req, timeout=args.timeout)
        else:
            _, body = request_json(origin, "/v1/chat/completions", req, timeout=args.timeout)
        timing = body.get("timings")
        timing = timing if isinstance(timing, dict) else {}
        choices = body.get("choices")
        item = choices[0] if isinstance(choices, list) and choices else {}
        item = item if isinstance(item, dict) else {}
        message = item.get("message")
        message = message if isinstance(message, dict) else {}
        answer = message.get("content")
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        details = usage.get("prompt_tokens_details")
        details = details if isinstance(details, dict) else {}
        receipt.update({
            "result": "completed" if isinstance(answer, str) and answer else "empty",
            "output_sha256": hashlib.sha256(answer.encode()).hexdigest()
            if isinstance(answer, str) else None,
            "matched_expected_ready": answer.strip().rstrip(".").strip() == "READY"
            if isinstance(answer, str) else False,
            "backend_counts": {
                "prompt_tokens": safe_int(usage, "prompt_tokens"),
                "completion_tokens": safe_int(usage, "completion_tokens"),
                "total_tokens": safe_int(usage, "total_tokens"),
                "cached_prompt_tokens": safe_int(details, "cached_tokens"),
                "timing_prompt_n": safe_int(timing, "prompt_n"),
                "timing_predicted_n": safe_int(timing, "predicted_n"),
                "timing_cache_n": safe_int(timing, "cache_n"),
            },
            "finish_reason": item.get("finish_reason") if item.get("finish_reason")
            in ("stop", "length", "tool_calls") else "unknown",
            "stream_done": body.get("stream_done") if args.stream else None,
            "first_content_ms": body.get("first_content_ms") if args.stream else None,
            "backend_reported_tps": timing.get("predicted_per_second"),
            "billing_cost_usd": None,
            "token_source": "openai-compatible-backend-usage",
            "accounting_note": "cache included in prompt tokens; backend usage is not billing proof",
        })
    except (urllib.error.URLError, ValueError, TimeoutError, json.JSONDecodeError) as exc:
        receipt["result"] = "unavailable"
        receipt["error_class"] = type(exc).__name__
    receipt["finished_utc"] = utc_now()
    receipt["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
    return receipt


def write_private(path: str, receipt):
    target = pathlib.Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = (json.dumps(receipt, sort_keys=True) + "\n").encode()
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--n-predict", type=int, default=16)
    parser.add_argument("--model", required=True, help="existing advertised model ID")
    parser.add_argument("--stream", action="store_true", help="require SSE terminal [DONE]")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--allow-private", action="store_true")
    parser.add_argument("--output", help="exclusive private JSON receipt path")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 60:
        parser.error("timeout must be 1..60 seconds")
    try:
        receipt = run(args)
    except ValueError as exc:
        parser.error(str(exc))
    if args.output:
        write_private(args.output, receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["result"] == "completed" and receipt.get("matched_expected_ready") else 3


if __name__ == "__main__":
    raise SystemExit(main())
