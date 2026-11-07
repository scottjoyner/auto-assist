#!/usr/bin/env python3
"""Non-admitting, signed one-shot LFM3 canary for a single Docker-private caller.

Not a reusable inference gateway. A fixed synthetic prompt is the ONLY POST
allowed, and replies never contain model text. Nothing reads or writes Tasks,
claims, graph, owner approvals, provider keys or WorkSupplyState. The HMAC key
is read from an inherited FD, never CLI, environment, on-disk or logs.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import os
import re
import subprocess
import time
from typing import Any
from urllib import error, request

MODEL_ALIAS = "lfm3-1.7b-x1-fast"
UPSTREAM = "http://127.0.0.1:19181"
PROMPT = {
    "model": MODEL_ALIAS,
    "messages": [{"role": "user", "content": "Reply OK."}],
    "max_tokens": 4,
    "temperature": 0,
    "stream": False,
}
MAX_BYTES = 512
MAX_RESPONSE = 64 * 1024


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in pairs:
        if name in out:
            raise ValueError("duplicate_field")
        out[name] = value
    return out


def _strict_json(data: bytes) -> Any:
    return json.loads(
        data, object_pairs_hook=_unique_pairs, parse_constant=lambda _x: (_ for _ in ()).throw(ValueError("nonfinite"))
    )


def signature(secret: bytes, timestamp: str, nonce: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    canonical = f"POST\n/canary\n{timestamp}\n{nonce}\n{digest}".encode("ascii")
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def verify_signed_request(
    secret: bytes, timestamp: str, nonce: str, submitted: str, body: bytes, seen: set[str], now: int
) -> str:
    if (
        not re.fullmatch(r"[0-9]{10}", timestamp or "")
        or not re.fullmatch(r"[0-9a-f]{32}", nonce or "")
        or not re.fullmatch(r"[0-9a-f]{64}", submitted or "")
        or abs(now - int(timestamp)) > 10
    ):
        raise ValueError("expired_or_malformed_signature")
    if nonce in seen:
        raise ValueError("replayed_nonce")
    expected = signature(secret, timestamp, nonce, body)
    if not hmac.compare_digest(expected, submitted):
        raise ValueError("invalid_signature")
    seen.add(nonce)
    return nonce


def _fixed_canary_body(data: bytes) -> None:
    if not (0 < len(data) <= MAX_BYTES):
        raise ValueError("non_canary_payload")
    supplied = _strict_json(data)
    if not isinstance(supplied, dict) or supplied != PROMPT:
        raise ValueError("non_canary_payload")
    # Python treats bool as int (False == 0 and True == 1). Reject type tricks
    # even when the apparent JSON value equals the synthetic fixed prompt.
    if (
        type(supplied.get("max_tokens")) is not int
        or type(supplied.get("temperature")) is not int
        or type(supplied.get("stream")) is not bool
        or type(supplied.get("model")) is not str
    ):
        raise ValueError("non_canary_payload")
    messages = supplied.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) != 1
        or not isinstance(messages[0], dict)
        or type(messages[0].get("role")) is not str
        or type(messages[0].get("content")) is not str
    ):
        raise ValueError("non_canary_payload")


def private_docker_bind(listen_ip: str, client_ip: str, interfaces: list[dict[str, Any]]) -> None:
    target = ipaddress.ip_address(listen_ip)
    caller = ipaddress.ip_address(client_ip)
    if (
        not isinstance(target, ipaddress.IPv4Address)
        or not isinstance(caller, ipaddress.IPv4Address)
        or not target.is_private
        or not caller.is_private
        or target.is_loopback
        or caller.is_loopback
        or target.is_link_local
        or caller.is_link_local
        or target == caller
    ):
        raise ValueError("invalid_docker_bridge_address")
    for iface in interfaces:
        if not isinstance(iface, dict):
            continue
        if not str(iface.get("ifname", "")).startswith("br-") and iface.get("ifname") != "docker0":
            continue
        for addr in iface.get("addr_info", []):
            if not isinstance(addr, dict) or addr.get("family") != "inet":
                continue
            if addr.get("local") != listen_ip:
                continue
            prefix = addr.get("prefixlen")
            if isinstance(prefix, int) and caller in ipaddress.ip_network(f"{listen_ip}/{prefix}", strict=False):
                return
    raise ValueError("docker_bridge_unverified")


def _fetch(opener: Any, method: str, route: str, data: bytes | None = None) -> Any:
    if method == "GET" and route not in ("/health", "/v1/models", "/slots"):
        raise ValueError("bad_upstream_get")
    if method == "POST" and route != "/v1/chat/completions":
        raise ValueError("bad_upstream_post")
    if method not in ("GET", "POST"):
        raise ValueError("bad_upstream_method")
    target = request.Request(
        UPSTREAM + route,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with opener.open(target, timeout=6) as reply:
        if reply.status != 200:
            raise ValueError("upstream_non200")
        raw = reply.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("upstream_oversized")
    return _strict_json(raw)


def perform_fixed_canary(opener: Any | None = None) -> dict[str, Any]:
    transport = opener or request.build_opener(request.ProxyHandler({}), _NoRedirect())
    health = _fetch(transport, "GET", "/health")
    models = _fetch(transport, "GET", "/v1/models")
    slots = _fetch(transport, "GET", "/slots")
    if (
        not isinstance(health, dict)
        or health.get("status") not in ("ok", "healthy")
        or not isinstance(models, dict)
        or not isinstance(models.get("data"), list)
        or len(models["data"]) != 1
        or not isinstance(models["data"][0], dict)
        or models["data"][0].get("id") != MODEL_ALIAS
        or not isinstance(slots, list)
        or len(slots) != 1
        or not isinstance(slots[0], dict)
        or slots[0].get("is_processing") is not False
    ):
        raise ValueError("unresolved_model_residency_or_slot")
    before = time.monotonic()
    upstream_body = json.dumps(PROMPT, separators=(",", ":")).encode()
    output = _fetch(transport, "POST", "/v1/chat/completions", upstream_body)
    if not isinstance(output, dict) or not isinstance(output.get("choices"), list) or len(output["choices"]) != 1:
        raise ValueError("invalid_inference_shape")
    usage = output.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("missing_token_usage")
    n = usage.get("completion_tokens")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 4:
        raise ValueError("inference_outside_budget")
    return {
        "contract": "lfm3-private-signed-canary-v1",
        "upstream_http": 200,
        "model_alias": MODEL_ALIAS,
        "completion_tokens": n,
        "elapsed_ms": round((time.monotonic() - before) * 1000),
        "task_admission": "HOLD",
        "production_inference_authorized": False,
        "resource_slots_approved": 0,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "LFM3Canary/1"
    sys_version = ""

    def log_message(self, *args: Any) -> None:
        pass

    def _reply(self, code: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, sort_keys=True).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def _reject(self, code: int, reason: str) -> None:
        self._reply(code, {"task_admission": "HOLD", "reason": reason})

    def do_GET(self) -> None:
        self.server.requests_seen += 1
        self._reject(405, "no_read_access")

    def do_POST(self) -> None:
        self.server.requests_seen += 1
        self.connection.settimeout(3)
        if self.client_address[0] != self.server.approved_client:
            return self._reject(403, "unapproved_source")
        if self.path != "/canary":
            return self._reject(404, "not_a_canary_route")
        lengths = self.headers.get_all("Content-Length", [])
        if (
            len(lengths) != 1
            or not lengths[0].isdigit()
            or not 0 < int(lengths[0]) <= MAX_BYTES
            or self.headers.get("Transfer-Encoding") is not None
            or self.headers.get("Authorization") is not None
            or self.headers.get("Content-Type") != "application/json"
        ):
            return self._reject(400, "invalid_envelope")
        signed_headers = ("X-Canary-Date", "X-Canary-Nonce", "X-Canary-Signature")
        if any(len(self.headers.get_all(key, [])) != 1 for key in signed_headers):
            return self._reject(403, "incomplete_signature")
        body = self.rfile.read(int(lengths[0]))
        if len(body) != int(lengths[0]):
            return self._reject(400, "truncated_body")
        try:
            verify_signed_request(
                self.server.key,
                self.headers["X-Canary-Date"],
                self.headers["X-Canary-Nonce"],
                self.headers["X-Canary-Signature"],
                body,
                self.server.used_nonces,
                int(time.time()),
            )
        except ValueError as exc:
            return self._reject(409 if str(exc) == "replayed_nonce" else 403, "replayed_or_invalid_signature")
        try:
            _fixed_canary_body(body)
        except (ValueError, UnicodeError):
            return self._reject(422, "not_a_fixed_canary")
        try:
            result = perform_fixed_canary()
        except (ValueError, OSError, TimeoutError, error.URLError):
            return self._reject(503, "physical_canary_failed")
        self._reply(200, result)

    def do_PUT(self) -> None:
        self.server.requests_seen += 1
        self._reject(405, "unsupported_method")

    do_PATCH = do_PUT
    do_DELETE = do_PUT


def run(listen_ip: str, client_ip: str, port: int, token_fd: int, max_requests: int = 4, max_seconds: int = 20) -> int:
    if os.geteuid() == 0 or not 1024 <= port <= 65535:
        raise ValueError("nonroot_high_port_required")
    if not 1 <= max_requests <= 6 or not 1 <= max_seconds <= 30:
        raise ValueError("bounded_one_shot_required")
    if token_fd < 3:
        raise ValueError("inherited_secret_fd_required")
    interfaces = json.loads(
        subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True, check=True, timeout=3).stdout
    )
    if not isinstance(interfaces, list):
        raise ValueError("interface_inventory_invalid")
    private_docker_bind(listen_ip, client_ip, interfaces)
    key = os.read(token_fd, 33)
    os.close(token_fd)
    if len(key) != 32:
        raise ValueError("one_shot_hmac_key_invalid")
    with HTTPServer((listen_ip, port), Handler) as server:
        server.approved_client = client_ip
        server.key = key
        server.used_nonces = set()
        server.requests_seen = 0
        server.timeout = 0.5
        print(
            json.dumps({"status": "SIGNED_CANARY_READY", "task_admission": "HOLD", "max_requests": max_requests}),
            flush=True,
        )
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline and server.requests_seen < max_requests:
            server.handle_request()
    print(json.dumps({"status": "CLOSED", "task_admission": "HOLD"}), flush=True)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-ip", required=True)
    parser.add_argument("--allowed-client", required=True)
    parser.add_argument("--port", type=int, default=19183)
    parser.add_argument("--token-fd", type=int, required=True)
    parser.add_argument("--max-requests", type=int, default=4)
    args = parser.parse_args()
    try:
        return run(args.listen_ip, args.allowed_client, args.port, args.token_fd, args.max_requests)
    except (ValueError, OSError, subprocess.SubprocessError):
        print(json.dumps({"status": "HOLD", "task_admission": "HOLD"}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
