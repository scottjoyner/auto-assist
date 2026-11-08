#!/usr/bin/env python3
"""One-shot, HMAC-authenticated Docker-private LFM3 + host-slot canary.

NON-ADMITTING RESEARCH ONLY. Accepts a fixed benign 4-token prompt; never
forwards arbitrary client inference or Task claims. A separate caller must
pre-provision an ephemeral *synthetic* ledger before --staging. Does not
create or sign a grant, install a daemon or authorize any production slot.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import multiprocessing
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# Load the reviewed host-local slot primitive by exact sibling repository path,
# without importing the top-level assistx package and its runtime side effects.
import importlib.util

_LEDGER_FILE = Path(__file__).resolve().parents[1] / "src" / "assistx" / "fleet_resource_slot_ledger.py"
if _LEDGER_FILE.is_symlink():
    raise RuntimeError("untrusted_ledger_source")
_SPEC = importlib.util.spec_from_file_location("_fixed_host_side_slot_ledger", _LEDGER_FILE)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MOD
_SPEC.loader.exec_module(_MOD)
FencedSlotLedger = _MOD.FencedSlotLedger
SlotLeaseDenied = _MOD.SlotLeaseDenied

from fleet_signed_ssh_docker_catalog_canary import validate_docker_bridge  # noqa: E402
from lfm3_signed_private_canary import (  # noqa: E402
    MAX_BYTES,
    _fixed_canary_body,
    perform_fixed_canary,
)

RUNTIME = "lfm3-x1-staging-only"
GENERATION = 897  # synthetic test fixture ONLY, not production authorization
MARKER = "0" * 64  # synthetic test fixture ONLY, no Ed25519 owner signature
MAX_REQUESTS = 4
MAX_SECONDS = 20
MAX_SLOT_MS = 11_000
MAX_CHILD_SECONDS = 8
SIGN_DOMAIN_ROUTE = "/staging-canary"


def sign(secret: bytes, timestamp: str, nonce: str, body: bytes) -> str:
    # Separate domain from the preexisting /canary probe, preventing a signed
    # token from authorizing a different local service or HTTP route.
    content_sha = hashlib.sha256(body).hexdigest()
    message = f"POST\n{SIGN_DOMAIN_ROUTE}\n{timestamp}\n{nonce}\n{content_sha}".encode("ascii")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_signed_request(
    secret: bytes,
    timestamp: str,
    nonce: str,
    submitted: str,
    body: bytes,
    seen: set[str],
    now: int,
) -> str:
    if (
        not re.fullmatch(r"[0-9]{10}", timestamp or "")
        or not re.fullmatch(r"[0-9a-f]{32}", nonce or "")
        or not re.fullmatch(r"[0-9a-f]{64}", submitted or "")
        or abs(now - int(timestamp)) > 10
    ):
        raise ValueError("invalid_or_stale_signed_envelope")
    if nonce in seen:
        raise ValueError("nonce_replay")
    if not hmac.compare_digest(sign(secret, timestamp, nonce, body), submitted):
        raise ValueError("wrong_signature")
    seen.add(nonce)
    return nonce


def _bounded_model_child(sender: Any) -> None:
    """Separate process may call ONLY fixed benign LFM3 proof and send counts."""
    try:
        evidence = perform_fixed_canary()
        tokens = evidence.get("completion_tokens") if isinstance(evidence, dict) else None
        if type(tokens) is not int or not 1 <= tokens <= 4:
            raise ValueError("fixed_model_tokens_invalid")
        sender.send({"completion_tokens": tokens})
    except Exception:
        try:
            sender.send({"error": "bounded_fixed_model_failed"})
        except (OSError, BrokenPipeError):
            pass
    finally:
        sender.close()


def run_bounded_fixed_inference() -> dict[str, int]:
    """Hard 8s child-process deadline, shorter than the 11s slot lease.

    On timeout, terminate/kill the HTTP client before releasing the slot.
    A remote upstream may continue work after disconnect, so full upstream
    exclusivity remains a separate release blocker.
    """
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    child = ctx.Process(target=_bounded_model_child, args=(sender,), daemon=True)
    started = False
    try:
        child.start()
        started = True
        sender.close()
        if not receiver.poll(MAX_CHILD_SECONDS):
            raise ValueError("model_child_deadline_exceeded")
        result = receiver.recv()
        child.join(timeout=0.4)
        if child.is_alive() or child.exitcode != 0:
            raise ValueError("model_child_did_not_finish_cleanly")
        if not isinstance(result, dict) or set(result) != {"completion_tokens"}:
            raise ValueError("model_child_result_invalid")
        return result
    except (OSError, EOFError, ValueError) as exc:
        raise ValueError("bounded_fixed_inference_unavailable") from exc
    finally:
        receiver.close()
        if started and child.is_alive():
            child.terminate()
            child.join(timeout=0.6)
            if child.is_alive():
                child.kill()
                child.join(timeout=0.6)
        elif not started:
            sender.close()


class PrivateHTTPServer(HTTPServer):
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = "LFM3SlotBridgeStaging/1"
    sys_version = ""

    def log_message(self, *_args: Any) -> None:
        pass  # Never log HMAC signatures or nonce/caller identities.

    def _reply(self, code: int, reason: str, *, tokens: int | None = None) -> None:
        content: dict[str, Any] = {
            "status": "STAGING_OBSERVED" if code == 200 else "HOLD",
            "reason": reason,
            "task_admission": "HOLD",
            "approved_parallel_slots": 0,
            "owner_signed": False,
            "production_inference_authorized": False,
            "production_resource_exclusivity_proven": False,
        }
        if code == 200 and type(tokens) is int and 1 <= tokens <= 4:
            content["completion_tokens"] = tokens
            content["host_slot_fenced_at_accept_and_result"] = True
        raw = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def do_GET(self) -> None:
        self.server.requests_seen += 1
        self._reply(405, "fixed_signed_post_only")

    def do_PUT(self) -> None:
        self.server.requests_seen += 1
        self._reply(405, "method_not_allowed")

    do_PATCH = do_PUT
    do_DELETE = do_PUT

    def do_POST(self) -> None:
        self.server.requests_seen += 1
        self.connection.settimeout(3)
        if self.client_address[0] != self.server.approved_client:
            return self._reply(403, "source_not_approved")
        if self.path != "/staging-canary":
            return self._reply(404, "path_not_approved")
        lengths = self.headers.get_all("Content-Length", [])
        signed = ("X-Canary-Date", "X-Canary-Nonce", "X-Canary-Signature")
        if (
            len(lengths) != 1
            or len(lengths[0]) > 4
            or not lengths[0].isdigit()
            or not 0 < int(lengths[0]) <= MAX_BYTES
            or len(self.headers.get_all("Content-Type", [])) != 1
            or any(len(self.headers.get_all(name, [])) != 1 for name in signed)
            or self.headers.get("Transfer-Encoding") is not None
            or self.headers.get("Authorization") is not None
            or self.headers.get("Content-Type") != "application/json"
            or self.headers.get("Content-Encoding") is not None
        ):
            return self._reply(400, "invalid_http_envelope")
        try:
            body = self.rfile.read(int(lengths[0]))
            if len(body) != int(lengths[0]):
                return self._reply(400, "truncated_body")
            _fixed_canary_body(body)
            nonce = verify_signed_request(
                self.server.secret,
                self.headers["X-Canary-Date"],
                self.headers["X-Canary-Nonce"],
                self.headers["X-Canary-Signature"],
                body,
                self.server.seen,
                int(time.time()),
            )
        except (ValueError, UnicodeError, OSError):
            return self._reply(403, "signed_fixed_canary_invalid_or_replayed")

        # Host-side CAS holds one externally-provisioned *synthetic* physical
        # staging slot around the ENTIRE upstream inference. The caller never
        # receives the lease token/epoch, so it cannot steal or release a slot.
        lease = None
        tokens = None
        slot_release_confirmed = False
        try:
            lease = self.server.ledger.acquire("staging-lfm3-fixed-prompt", nonce, duration_ms=MAX_SLOT_MS)
            self.server.ledger.validate_effect(lease)
            result = run_bounded_fixed_inference()  # killable 8s fixed-prompt child
            # An owner grant/slot revoked during model generation must not
            # produce a positive result. Fences stale COMMIT-like responses.
            self.server.ledger.validate_effect(lease)
            tokens = result.get("completion_tokens")
            if type(tokens) is not int or not 1 <= tokens <= 4:
                raise ValueError("invalid_physical_token_count")
        except (SlotLeaseDenied, ValueError, OSError, TimeoutError):
            tokens = None
        finally:
            if lease is not None:
                try:
                    self.server.ledger.release(lease)
                    slot_release_confirmed = True
                except SlotLeaseDenied:
                    slot_release_confirmed = False  # revoked/expired: never free successor
        # Do NOT emit success until the host's resource-owned release CAS is
        # complete. This still isn't a distributed production Task COMMIT.
        if slot_release_confirmed and type(tokens) is int and 1 <= tokens <= 4:
            return self._reply(200, "signed_private_slot_fenced_staging_witness", tokens=tokens)
        return self._reply(423, "host_slot_or_physical_model_unavailable")


def serve(
    bind_ip: str,
    approved_client: str,
    port: int,
    secret_fd: int,
    ledger_path: Path,
    *,
    max_requests: int = MAX_REQUESTS,
    max_seconds: int = MAX_SECONDS,
) -> int:
    if (
        os.geteuid() == 0
        or secret_fd < 3
        or not 1024 <= port <= 65535
        or not 1 <= max_requests <= MAX_REQUESTS
        or not 1 <= max_seconds <= MAX_SECONDS
        or not ledger_path.is_absolute()
    ):
        raise ValueError("bounded_nonroot_staging_required")
    inventory = json.loads(subprocess.check_output(["ip", "-j", "-4", "addr", "show"], timeout=4))
    if not isinstance(inventory, list):
        raise ValueError("bridge_inventory_invalid")
    validate_docker_bridge(bind_ip, approved_client, inventory)
    secret = os.read(secret_fd, 33)
    os.close(secret_fd)
    if len(secret) != 32:
        raise ValueError("inherited_one_shot_secret_invalid")
    # There is no provisioner or trust root here. A separate operator-created
    # synthetic fixture may be used ONLY by this explicitly staging binary.
    ledger = FencedSlotLedger(
        ledger_path,
        runtime_id=RUNTIME,
        generation=GENERATION,
        capacity=1,
        owner_receipt_sha256=MARKER,
    )
    # Source of truth must preexist and be private; validate BEFORE binding.
    ledger._transact(lambda conn: ledger._grant_and_slots(conn, int(time.time() * 1000)))
    with PrivateHTTPServer((bind_ip, port), Handler) as server:
        server.approved_client = approved_client
        server.secret = secret
        server.ledger = ledger
        server.seen = set()
        server.requests_seen = 0
        server.timeout = 0.25
        print(json.dumps({"status": "SLOT_HMAC_STAGE_READY", "task_admission": "HOLD"}), flush=True)
        end = time.monotonic() + max_seconds
        while time.monotonic() < end and server.requests_seen < max_requests:
            server.handle_request()
    print(json.dumps({"status": "CLOSED", "task_admission": "HOLD"}), flush=True)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", action="store_true")
    parser.add_argument("--listen-ip")
    parser.add_argument("--client-ip")
    parser.add_argument("--port", type=int, default=19203)
    parser.add_argument("--secret-fd", type=int)
    parser.add_argument("--ledger-path", type=Path)
    parser.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    parser.add_argument("--max-seconds", type=int, default=MAX_SECONDS)
    args = parser.parse_args(argv)
    if not args.staging:
        print(json.dumps({"status": "HOLD", "reason": "EXPLICIT_STAGING_ONLY", "task_admission": "HOLD"}))
        return 2
    try:
        if args.secret_fd is None or not args.listen_ip or not args.client_ip or args.ledger_path is None:
            raise ValueError("missing_staging_inputs")
        return serve(
            args.listen_ip,
            args.client_ip,
            args.port,
            args.secret_fd,
            args.ledger_path,
            max_requests=args.max_requests,
            max_seconds=args.max_seconds,
        )
    except (ValueError, OSError, SlotLeaseDenied, subprocess.SubprocessError, json.JSONDecodeError):
        print(json.dumps({"status": "HOLD", "reason": "SLOT_HMAC_STAGE_NOT_READY", "task_admission": "HOLD"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
