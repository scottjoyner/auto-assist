#!/usr/bin/env python3
"""Non-admitting LFM3 namespace co-resident HMAC gateway + host-slot fence.

This module runs ONLY inside a separately isolated one-shot container sharing
its network namespace with a fixed model server bound to loopback. No grant
creation, Task claims, model forwarding APIs, config mutations or daemon.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import multiprocessing
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib import request

import fleet_lfm3_signed_slot_bridge_canary as signed_slot

MODEL_ALIAS = "lfm3-isolated-namespace-poc"
UPSTREAM = "http://127.0.0.1:19185"
MAX_BYTES = 16_384
MAX_REQUESTS = 4
MAX_SECONDS = 16
MAX_CHILD_SECONDS = 8
HMAC_PORT = 19205


def _fetch_fixed(opener: Any, method: str, path: str, body: bytes | None = None) -> Any:
    if (method, path) not in {
        ("GET", "/health"),
        ("GET", "/v1/models"),
        ("GET", "/slots"),
        ("POST", "/v1/chat/completions"),
    }:
        raise ValueError("nonfixed_model_route")
    req = request.Request(
        UPSTREAM + path,
        method=method,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with opener.open(req, timeout=5) as response:
        if response.status != 200:
            raise ValueError("upstream_not_healthy")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("upstream_response_unbounded")
    return json.loads(raw, parse_constant=lambda _x: (_ for _ in ()).throw(ValueError("nonfinite_json")))


def fixed_namespaced_model() -> dict[str, int]:
    """Never accepts user prompt, model, URL, token cap or tool arguments."""
    opener = (
        request.build_opener(request.ProxyHandler({}), signed_slot.lfm3_signed_private_canary._NoRedirect())
        if hasattr(signed_slot, "lfm3_signed_private_canary")
        else request.build_opener(request.ProxyHandler({}), _NoRedirect())
    )
    health = _fetch_fixed(opener, "GET", "/health")
    catalog = _fetch_fixed(opener, "GET", "/v1/models")
    slots = _fetch_fixed(opener, "GET", "/slots")
    if (
        not isinstance(health, dict)
        or health.get("status") != "ok"
        or not isinstance(catalog, dict)
        or not isinstance(catalog.get("data"), list)
        or len(catalog["data"]) != 1
        or not isinstance(catalog["data"][0], dict)
        or catalog["data"][0].get("id") != MODEL_ALIAS
        or not isinstance(slots, list)
        or len(slots) != 1
        or not isinstance(slots[0], dict)
        or slots[0].get("is_processing") is not False
    ):
        raise ValueError("unverified_private_model_or_busy_slot")
    fixed_body = {
        "model": MODEL_ALIAS,
        "messages": [{"role": "user", "content": "Reply OK."}],
        "temperature": 0,
        "max_tokens": 4,
        "stream": False,
    }
    result = _fetch_fixed(
        opener, "POST", "/v1/chat/completions", json.dumps(fixed_body, separators=(",", ":")).encode()
    )
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("choices"), list)
        or len(result["choices"]) != 1
        or not isinstance(result.get("usage"), dict)
    ):
        raise ValueError("invalid_private_model_response")
    tokens = result["usage"].get("completion_tokens")
    if type(tokens) is not int or not 1 <= tokens <= 4:
        raise ValueError("invalid_token_count")
    return {"completion_tokens": tokens}


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _model_child(sender: Any) -> None:
    try:
        sender.send(fixed_namespaced_model())
    except Exception:
        try:
            sender.send({"error": "namespaced_model_failed"})
        except (OSError, BrokenPipeError):
            pass
    finally:
        sender.close()


def run_bounded_namespaced_inference() -> dict[str, int]:
    """Force a fresh killable process with deadline < resource-slot expiry."""
    ctx = multiprocessing.get_context("spawn")
    reader, writer = ctx.Pipe(duplex=False)
    child = ctx.Process(target=_model_child, args=(writer,), daemon=True)
    started = False
    try:
        child.start()
        started = True
        writer.close()
        if not reader.poll(MAX_CHILD_SECONDS):
            raise ValueError("namespace_model_deadline")
        result = reader.recv()
        child.join(timeout=0.5)
        if child.is_alive() or child.exitcode != 0:
            raise ValueError("namespace_model_not_quiescent")
        if (
            not isinstance(result, dict)
            or set(result) != {"completion_tokens"}
            or type(result["completion_tokens"]) is not int
            or not 1 <= result["completion_tokens"] <= 4
        ):
            raise ValueError("namespace_model_invalid_result")
        return result
    except (OSError, EOFError, ValueError) as exc:
        raise ValueError("bounded_namespaced_model_failed") from exc
    finally:
        reader.close()
        if started and child.is_alive():
            child.terminate()
            child.join(timeout=0.6)
            if child.is_alive():
                child.kill()
                child.join(timeout=0.6)
        elif not started:
            writer.close()


def verify_isolated_namespace(bind_ip: str, approved_client: str, port: int) -> None:
    """Model is internal loopback-only; gateway binds distinct container IP.

    Operator independently checks Docker inspect for both model and gateway:
    both must share namespace, no model published ports and no host network.
    """
    try:
        bind = ipaddress.ip_address(bind_ip)
        caller = ipaddress.ip_address(approved_client)
        if (
            bind.version != 4
            or caller.version != 4
            or not bind.is_private
            or not caller.is_private
            or bind.is_loopback
            or caller.is_loopback
            or bind == caller
            or not 1024 <= port <= 65535
        ):
            raise ValueError("invalid_private_namespace_identity")
        with socket.socket() as sock:
            sock.bind((bind_ip, 0))  # Must actually belong to THIS namespace.
        with socket.socket() as sock:
            sock.settimeout(0.25)
            # No listener on eth0/private Docker network at model port.
            if sock.connect_ex((bind_ip, 19185)) == 0:
                raise ValueError("model_exposed_on_namespace_interface")
        with socket.socket() as sock:
            sock.settimeout(0.4)
            # The model itself MUST be ready on private loopback.
            if sock.connect_ex(("127.0.0.1", 19185)) != 0:
                raise ValueError("model_not_on_private_loopback")
    except (OSError, ValueError) as exc:
        raise ValueError("unverified_namespaced_inference_topology") from exc


def serve(
    bind_ip: str,
    caller: str,
    ledger_path: Path,
    *,
    secret_fd: int = 0,
    port: int = HMAC_PORT,
    max_seconds: int = MAX_SECONDS,
    max_requests: int = MAX_REQUESTS,
) -> int:
    if (
        os.geteuid() == 0
        or secret_fd != 0
        or not ledger_path.is_absolute()
        or not 1 <= max_seconds <= MAX_SECONDS
        or not 1 <= max_requests <= MAX_REQUESTS
    ):
        raise ValueError("nonroot_bounded_stdin_staging_only")
    verify_isolated_namespace(bind_ip, caller, port)
    secret = os.read(secret_fd, 33)  # ephemeral 32-byte HMAC, never in env/argv/disk
    if len(secret) != 32:
        raise ValueError("invalid_ephemeral_hmac_secret")
    ledger = signed_slot.FencedSlotLedger(
        ledger_path,
        runtime_id=signed_slot.RUNTIME,
        generation=signed_slot.GENERATION,
        capacity=1,
        owner_receipt_sha256=signed_slot.MARKER,
    )
    # Must be externally provisioned fixture, trusted UID/custody and grant
    # verified BEFORE binding an interface. No self-provision mechanism.
    ledger._transact(lambda conn: ledger._grant_and_slots(conn, int(time.time() * 1000)))
    # Reuse source-reviewed #578 HTTP/HMAC/claim/release semantics while
    # selecting the *namespaced-only* fixed model callback for the child.
    signed_slot.run_bounded_fixed_inference = run_bounded_namespaced_inference
    with signed_slot.PrivateHTTPServer((bind_ip, port), signed_slot.Handler) as server:
        server.approved_client = caller
        server.secret = secret
        server.ledger = ledger
        server.seen = set()
        server.requests_seen = 0
        server.timeout = 0.25
        print(json.dumps({"status": "ISOLATED_MODEL_SLOT_GATEWAY_READY", "task_admission": "HOLD"}), flush=True)
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline and server.requests_seen < max_requests:
            server.handle_request()
    print(json.dumps({"status": "CLOSED", "task_admission": "HOLD"}), flush=True)
    return 2  # deliberately nonauthorizing even on staging success


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--staging", action="store_true")
    p.add_argument("--bind-ip")
    p.add_argument("--client-ip")
    p.add_argument("--ledger-path", type=Path)
    p.add_argument("--port", type=int, default=HMAC_PORT)
    p.add_argument("--max-seconds", type=int, default=MAX_SECONDS)
    p.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    a = p.parse_args(argv)
    if not a.staging:
        print(json.dumps({"status": "HOLD", "reason": "EXPLICIT_ISOLATED_STAGING_ONLY", "task_admission": "HOLD"}))
        return 2
    try:
        if a.bind_ip is None or a.client_ip is None or a.ledger_path is None:
            raise ValueError("missing_verified_topology")
        return serve(
            a.bind_ip, a.client_ip, a.ledger_path, port=a.port, max_seconds=a.max_seconds, max_requests=a.max_requests
        )
    except (ValueError, OSError, signed_slot.SlotLeaseDenied):
        print(
            json.dumps({"status": "HOLD", "reason": "ISOLATED_STAGE_NOT_READY", "task_admission": "HOLD"}), flush=True
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
