#!/usr/bin/env python3
"""Single-process HMAC-protected Docker-private SSH-LAN CATALOG canary.

Read-only synthetic staging evidence. No model generation, claims, approval,
provider connection, persistent process, SSH key export or production gateway.
An ephemeral 32-byte HMAC secret arrives on an inherited file descriptor.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from fleet_ssh_lan_model_readonly_witness import check_node, TARGETS

MAX_POST_BYTES = 64
MAX_REQUESTS = 7
MAX_SECONDS = 30


def sign(secret: bytes, timestamp: str, nonce: str, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    message = f"POST\n/canary\n{timestamp}\n{nonce}\n{digest}".encode("ascii")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def check_signature(secret: bytes, timestamp: str, nonce: str,
                    signature: str, data: bytes, seen: set[str], now: int) -> None:
    if (not re.fullmatch(r"[0-9]{10}", timestamp or "")
            or not re.fullmatch(r"[0-9a-f]{32}", nonce or "")
            or not re.fullmatch(r"[0-9a-f]{64}", signature or "")
            or abs(now-int(timestamp)) > 10):
        raise ValueError("stale_or_invalid_signature")
    if nonce in seen:
        raise ValueError("nonce_replayed")
    if not hmac.compare_digest(sign(secret, timestamp, nonce, data), signature):
        raise ValueError("signature_mismatch")
    seen.add(nonce)


def parse_fixed_body(body: bytes) -> str:
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_POST_BYTES:
        raise ValueError("invalid_body_size")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_field")
            value[key] = item
        return value

    try:
        obj = json.loads(body, object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_body_json") from exc
    if not isinstance(obj, dict) or set(obj) != {"node"}:
        raise ValueError("unexpected_fields")
    node = obj["node"]
    if type(node) is not str or node not in TARGETS:
        raise ValueError("node_not_allowlisted")
    return node


def validate_docker_bridge(bind_ip: str, client_ip: str, interfaces: list[dict]) -> None:
    host = ipaddress.ip_address(bind_ip)
    source = ipaddress.ip_address(client_ip)
    if (not isinstance(host, ipaddress.IPv4Address)
            or not isinstance(source, ipaddress.IPv4Address)
            or not host.is_private or not source.is_private
            or host.is_loopback or source.is_loopback or host == source):
        raise ValueError("nonprivate_or_ambiguous_bind")
    for interface in interfaces:
        if not isinstance(interface, dict):
            continue
        name = interface.get("ifname")
        if type(name) is not str or not (name.startswith("br-") or name == "docker0"):
            continue
        addresses = interface.get("addr_info")
        if not isinstance(addresses, list):
            continue
        for address in addresses:
            if not isinstance(address, dict) or address.get("family") != "inet":
                continue
            if address.get("local") != bind_ip:
                continue
            prefix = address.get("prefixlen")
            if type(prefix) is int and 1 <= prefix <= 32 and source in ipaddress.ip_network(
                f"{bind_ip}/{prefix}", strict=False
            ):
                return
    raise ValueError("docker_private_bind_not_attested")


class Handler(BaseHTTPRequestHandler):
    server_version = "SSHPhysicalLANCanary/1"
    sys_version = ""

    def log_message(self, *_args: Any) -> None:
        pass

    def _reply(self, status: int, data: dict[str, Any]) -> None:
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def _deny(self, status: int, reason: str) -> None:
        self._reply(status, {"status": "HOLD", "task_admission": "HOLD", "reason": reason})

    def do_GET(self) -> None:
        self.server.requests_seen += 1
        self._deny(405, "catalog_post_only")

    def do_POST(self) -> None:
        self.server.requests_seen += 1
        self.connection.settimeout(3)
        if self.client_address[0] != self.server.allowed_client:
            return self._deny(403, "source_not_allowed")
        if self.path != "/canary":
            return self._deny(404, "route_not_allowed")
        lengths = self.headers.get_all("Content-Length", [])
        if (len(lengths) != 1 or not lengths[0].isdigit()
                or not 0 < int(lengths[0]) <= MAX_POST_BYTES
                or self.headers.get("Transfer-Encoding") is not None
                or self.headers.get("Authorization") is not None
                or self.headers.get("Content-Type") != "application/json"):
            return self._deny(400, "invalid_http_envelope")
        try:
            body = self.rfile.read(int(lengths[0]))
            node = parse_fixed_body(body)
            check_signature(self.server.secret,
                            self.headers.get("X-Canary-Timestamp", ""),
                            self.headers.get("X-Canary-Nonce", ""),
                            self.headers.get("X-Canary-Signature", ""),
                            body, self.server.seen, int(time.time()))
        except (ValueError, OSError):
            return self._deny(403, "invalid_signed_canary")
        try:
            evidence = check_node(node)
        except (ValueError, OSError, TimeoutError, subprocess.SubprocessError):
            return self._deny(424, "ssh_lan_evidence_unavailable")
        self._reply(200, {"status": "OBSERVED_NON_ADMITTING", "node": node,
                          "catalog_http": 200,
                          "ssh_over_physical_lan_verified": True,
                          "exact_expected_model": bool(evidence["expected_model_present"]),
                          "model_count": evidence["advertised_unique_models"],
                          "direct_lan_http_verified": False,
                          "production_ingress_authorized": False,
                          "approved_parallel_slots": 0, "task_admission": "HOLD"})

    def do_PUT(self) -> None:
        self.server.requests_seen += 1
        self._deny(405, "method_not_allowed")


class PrivateServer(HTTPServer):
    allow_reuse_address = False


def serve(bind_ip: str, client_ip: str, port: int, secret_fd: int,
          *, max_requests: int = MAX_REQUESTS, seconds: int = MAX_SECONDS) -> int:
    if secret_fd < 3 or not 1 <= port <= 65535 or not 1 <= max_requests <= 7 or not 1 <= seconds <= 30:
        raise ValueError("invalid_staging_limits")
    interfaces = json.loads(subprocess.check_output(
        ["ip", "-j", "-4", "addr", "show"], timeout=4))
    validate_docker_bridge(bind_ip, client_ip, interfaces)
    secret = os.read(secret_fd, 33)
    os.close(secret_fd)
    if len(secret) != 32:
        raise ValueError("invalid_ephemeral_secret")
    with PrivateServer((bind_ip,port), Handler) as server:
        server.allowed_client = client_ip
        server.secret = secret
        server.seen = set()
        server.requests_seen = 0
        server.timeout = 0.4
        cutoff = time.monotonic() + seconds
        while time.monotonic() < cutoff and server.requests_seen < max_requests:
            server.handle_request()
    return 2  # Normal termination still means Task HOLD.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--listen-ip", required=False)
    parser.add_argument("--client-ip", required=False)
    parser.add_argument("--port", type=int, default=19201)
    parser.add_argument("--secret-fd", type=int)
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    parser.add_argument("--max-seconds", type=int, default=MAX_SECONDS)
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({"status": "HOLD", "reason": "LIVE_CANARY_NOT_REQUESTED",
                          "task_admission": "HOLD", "approved_parallel_slots": 0}))
        return 2
    try:
        if not args.listen_ip or not args.client_ip or args.secret_fd is None:
            raise ValueError("missing_private_bind")
        return serve(args.listen_ip, args.client_ip,args.port,args.secret_fd,
                     max_requests=args.max_requests,seconds=args.max_seconds)
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError):
        print(json.dumps({"status": "HOLD", "reason": "SIGNED_PRIVATE_CANARY_NOT_VERIFIED",
                          "task_admission": "HOLD", "approved_parallel_slots": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
