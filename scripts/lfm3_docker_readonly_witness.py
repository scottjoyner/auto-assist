#!/usr/bin/env python3
"""Ephemeral Docker-private LFM3 metadata witness, NEVER an inference proxy.

Only a pinned private Docker client can GET /readiness. No other route, POST,
Task, graph, signer or credential path exists. A 200 is NOT work admission.
Run with a bounded --max-requests and short deadline. No daemon/install mode.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib import request

EXPECTED_MODELS = {19180: "lfm3-local-test", 19181: "lfm3-1.7b-x1-fast"}
MAX_BODY_BYTES = 128 * 1024


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get_json(opener: Any, upstream_port: int, endpoint: str) -> Any:
    if upstream_port not in EXPECTED_MODELS or endpoint not in ("/health", "/v1/models", "/slots"):
        raise ValueError("unapproved_upstream")
    url = f"http://127.0.0.1:{upstream_port}{endpoint}"
    with opener.open(request.Request(url, method="GET"), timeout=2.5) as response:
        if response.status != 200:
            raise ValueError("upstream_non_200")
        raw = response.read(MAX_BODY_BYTES + 1)
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("upstream_oversized")
    return json.loads(raw)


def read_witness(upstream_port: int, opener: Any | None = None) -> dict[str, Any]:
    if upstream_port not in EXPECTED_MODELS:
        raise ValueError("unapproved_model_port")
    transport = opener or request.build_opener(request.ProxyHandler({}), NoRedirect())
    health = _get_json(transport, upstream_port, "/health")
    models = _get_json(transport, upstream_port, "/v1/models")
    slots = _get_json(transport, upstream_port, "/slots")
    if not isinstance(health, dict) or not isinstance(models, dict) or not isinstance(slots, list):
        raise ValueError("invalid_upstream_shape")
    health_status = health.get("status")
    if not isinstance(health_status, str) or health_status not in ("ok", "healthy"):
        raise ValueError("upstream_not_healthy")
    entries = models.get("data")
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError("unresolved_loaded_model")
    if entries[0].get("id") != EXPECTED_MODELS[upstream_port] or not (1 <= len(slots) <= 16):
        raise ValueError("model_or_slot_mismatch")
    if not all(isinstance(slot, dict) and isinstance(slot.get("is_processing"), bool)
               for slot in slots):
        raise ValueError("slot_state_unresolved")
    idle = sum(not s["is_processing"] for s in slots)
    return {
        "contract": "lfm3-docker-readonly-witness-v1",
        "model_alias": EXPECTED_MODELS[upstream_port],
        "upstream_health_http": 200,
        "configured_slots": len(slots),
        "idle_slots": idle,
        "physical_model_artifact_signed": False,
        "capacity_approved": False,
        "task_admission": "HOLD",
    }


def private_interface_ipv4(value: str) -> str:
    address = ipaddress.ip_address(value)
    if (not isinstance(address, ipaddress.IPv4Address)
            or address.is_loopback or address.is_unspecified
            or address.is_multicast or address.is_link_local
            or not address.is_private):
        raise ValueError("private_docker_ipv4_required")
    return str(address)


class WitnessHandler(BaseHTTPRequestHandler):
    server_version = "LFM3Witness/1"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass  # No tokens, client payloads, request query strings or paths logged.

    def _json(self, status: int, payload: dict[str, Any]):
        raw = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _deny(self, status: int, code: str):
        self._json(status, {"status": "HOLD", "reason": code, "task_admission": "HOLD"})

    def do_GET(self):
        self.server.requests_seen += 1
        if self.client_address[0] != self.server.allowed_client:
            return self._deny(403, "unapproved_client")
        if self.path != "/readiness":
            return self._deny(404, "unapproved_route")
        try:
            body = read_witness(self.server.upstream_port)
        except (ValueError, OSError, TimeoutError, json.JSONDecodeError):
            return self._deny(503, "upstream_evidence_incomplete")
        self._json(200, body)

    def do_POST(self):
        self.server.requests_seen += 1
        self._deny(405, "no_mutating_methods")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def verify_docker_bridge(listen_ip: str, allowed_client: str,
                         interfaces: list[dict[str, Any]]) -> None:
    """Never bind a witness to a LAN/Tailnet interface, even by accident."""
    listen = ipaddress.ip_address(private_interface_ipv4(listen_ip))
    client = ipaddress.ip_address(private_interface_ipv4(allowed_client))
    if listen == client:
        raise ValueError("source_must_differ_from_gateway")
    for iface in interfaces:
        if not isinstance(iface, dict):
            continue
        name = iface.get("ifname", "")
        if not isinstance(name, str) or not (name.startswith("br-") or name == "docker0"):
            continue
        addresses = iface.get("addr_info")
        if not isinstance(addresses, list):
            continue
        for item in addresses:
            if not isinstance(item, dict) or item.get("family") != "inet":
                continue
            if item.get("local") != str(listen):
                continue
            prefix = item.get("prefixlen")
            if type(prefix) is int and 1 <= prefix <= 32 and client in ipaddress.ip_network(
                f"{listen}/{prefix}", strict=False
            ):
                return
    raise ValueError("not_a_matching_private_docker_bridge")


def run_server(listen_ip: str, allowed_client: str, port: int,
               upstream_port: int, max_requests: int, max_seconds: float = 20.0) -> int:
    private_interface_ipv4(listen_ip)
    private_interface_ipv4(allowed_client)
    if port < 1024 or port > 65535 or upstream_port not in EXPECTED_MODELS:
        raise ValueError("invalid_port_or_model")
    if not 1 <= max_requests <= 8 or not 1 <= max_seconds <= 30:
        raise ValueError("unbounded_witness")
    if os.geteuid() == 0:
        raise ValueError("root_witness_forbidden")
    try:
        interfaces = json.loads(subprocess.run(
            ["ip", "-j", "-4", "addr", "show"],
            check=True, capture_output=True, text=True, timeout=3,
        ).stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("docker_bridge_identity_unverified") from exc
    if not isinstance(interfaces, list):
        raise ValueError("docker_bridge_identity_unverified")
    verify_docker_bridge(listen_ip, allowed_client, interfaces)
    with HTTPServer((listen_ip, port), WitnessHandler) as server:
        server.allowed_client = allowed_client
        server.upstream_port = upstream_port
        server.requests_seen = 0
        server.timeout = 1
        print(json.dumps({"status": "READY_FOR_READ_ONLY_WITNESS", "bind": listen_ip,
                          "port": port, "max_requests": max_requests,
                          "task_admission": "HOLD"}), flush=True)
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline and server.requests_seen < max_requests:
            server.handle_request()
    print(json.dumps({"status": "CLOSED", "task_admission": "HOLD"}), flush=True)
    return 2  # Never an admission grant.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-ip", required=True)
    parser.add_argument("--allowed-client", required=True)
    parser.add_argument("--port", type=int, default=19182)
    parser.add_argument("--upstream-port", type=int, choices=sorted(EXPECTED_MODELS), default=19181)
    parser.add_argument("--max-requests", type=int, default=3)
    options = parser.parse_args()
    try:
        return run_server(options.listen_ip, options.allowed_client,
                          options.port, options.upstream_port, options.max_requests)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "HOLD", "error_type": type(exc).__name__,
                          "task_admission": "HOLD"}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
