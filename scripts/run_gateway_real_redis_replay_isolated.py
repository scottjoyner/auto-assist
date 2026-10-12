#!/usr/bin/env python3
"""Guarded, disposable real Redis signed-gateway replay negative acceptance.

Only one fresh, read-only --network none Redis container; no live API or
secrets, users, providers, Tailscale, remote network, persistent volumes,
graph, shell profile, or service configuration. All keys are generated
in memory and are SYNTHETIC.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
NAME = "assistx-gateway-replay-disposable-20261009"
IMAGE = "redis:7-alpine"
PIN = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
CID = re.compile(r"^[0-9a-f]{64}$")


def command(*parts: str, timeout: int = 12) -> str:
    result = subprocess.run(
        parts, capture_output=True, timeout=timeout, check=False,
    )
    if result.returncode:
        raise RuntimeError("disposable command failed")
    return result.stdout.decode("utf-8", "replace").strip()


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "src" / "assistx" / (name + ".py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("research source missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class DockerRedis:
    def __init__(self, cid: str):
        self.cid = cid

    def cli(self, *args: str) -> str:
        return command("docker", "exec", self.cid, "redis-cli", "--raw", *args)

    def info(self, section="server") -> dict[str, str]:
        if section != "server":
            raise RuntimeError("only read-only Redis boot check allowed")
        return dict(
            line.split(":", 1) for line in self.cli("INFO", "server").splitlines()
            if ":" in line and not line.startswith("#")
        )

    def eval(self, script: str, keys: int, name: str, expiration: int) -> int:
        result = self.cli("EVAL", script, str(keys), name, str(expiration))
        if result not in ("-1", "0", "1"):
            raise RuntimeError("unknown nonce store response")
        return int(result)


def assert_topology(cid: str, digest: str) -> None:
    obj = json.loads(command("docker", "inspect", "--type", "container", cid))[0]
    host = obj.get("HostConfig") or {}
    if (
        obj.get("Id") != cid or obj.get("Name") != "/" + NAME
        or obj.get("Image") != digest or not obj.get("State", {}).get("Running")
        or host.get("NetworkMode") != "none"
        or not host.get("ReadonlyRootfs")
        or bool(host.get("PortBindings")) or bool(host.get("Binds"))
        or bool(obj.get("Mounts")) or host.get("Privileged")
        or host.get("Memory", 0) > 128 * 1024**2
        or host.get("NanoCpus", 0) > 250_000_000
    ):
        raise RuntimeError("untrusted disposable Redis topology")


def trial(cid: str, expected_image_id: str) -> None:
    verifier = load("trusted_gateway_identity_contract")
    store_lib = load("gateway_replay_fence_research")
    redis = DockerRedis(cid)
    for _ in range(20):
        try:
            if redis.cli("PING") == "PONG":
                break
        except RuntimeError:
            time.sleep(.1)
    else:
        raise RuntimeError("isolated Redis failed startup")
    pin = redis.info()["run_id"].strip()
    if not PIN.fullmatch(pin):
        raise RuntimeError("unverified Redis server generation")
    receiver = store_lib.GatewayReplayFence(
        redis, "synthetic-receiver-key-never-production-xyz-012345", pin
    )
    now_ms = int(time.time() * 1000)
    subject = "synthetic-device@example.invalid"
    claim = {
        "version": verifier.VERSION, "issuer": verifier.ISSUER,
        "audience": verifier.AUDIENCE, "key_id": "synthetic-private-test-only",
        "subject": subject, "issued_at_ms": now_ms - 500,
        "expires_at_ms": now_ms + 20_000, "nonce": secrets.token_hex(16),
        "method": "GET", "target": "/api/traces?limit=1",
        "scope": "trace:read",
    }
    signer = Ed25519PrivateKey.generate()
    message = verifier.canonical_claim(claim)
    sig = signer.sign(verifier.PREFIX + message)
    pub = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    def verified() -> bool:
        try:
            principal = verifier.verify_gateway_assertion(
                claim_bytes=message, signature=sig,
                receiver_public_key=pub, receiver_key_id=claim["key_id"],
                now_ms=now_ms, request_method="GET",
                request_target=claim["target"], required_scope="trace:read",
                consume_nonce_once=receiver.consume,
            )
            return principal == subject
        except verifier.GatewayIdentityDenied:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        admitted = list(pool.map(lambda _: verified(), range(10)))
    if admitted.count(True) != 1 or admitted.count(False) != 9:
        raise RuntimeError("ten-worker real Redis replay gate not atomic")
    print("REAL_REDIS_10_WORKER_SINGLE_USE_GATEWAY_IDENTITY_PASS", flush=True)

    # Additional RESEARCH-ONLY HTTP/ASGI acceptance against the same disposable
    # Redis nonce fence. Load a pure submodule without importing AssistX API or
    # reading any production configuration/credentials.
    import base64
    import types
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.testclient import TestClient
    if "assistx" not in sys.modules:
        package = types.ModuleType("assistx")
        package.__path__ = [str(ROOT / "src" / "assistx")]
        sys.modules["assistx"] = package
    from assistx.signed_observation_asgi_research import (
        GatewayIdentityDenied, verify_observation_asgi_identity,
    )
    observation = "/api/v1/runtime/observations"
    receipt = dict(claim)
    receipt["nonce"] = secrets.token_hex(16)
    receipt["target"] = observation
    receipt["scope"] = "model:observe"
    receipt["issued_at_ms"] = int(time.time() * 1000) - 500
    receipt["expires_at_ms"] = receipt["issued_at_ms"] + 20_000
    receipt_data = verifier.canonical_claim(receipt)
    receipt_signature = signer.sign(verifier.PREFIX + receipt_data)
    signed_headers = {
        "X-AssistX-Gateway-Claim": base64.urlsafe_b64encode(
            receipt_data).rstrip(b"=").decode("ascii"),
        "X-AssistX-Gateway-Signature": base64.urlsafe_b64encode(
            receipt_signature).rstrip(b"=").decode("ascii"),
    }
    app = FastAPI()

    def synthetic_observation(request: Request):
        try:
            verify_observation_asgi_identity(
                scope=request.scope,
                headers=request.scope["headers"],
                receiver_public_key=pub,
                receiver_key_id=receipt["key_id"],
                now_ms=int(time.time() * 1000),
                consume_nonce_once=receiver.consume,
            )
        except GatewayIdentityDenied:
            raise HTTPException(status_code=401, detail="signed identity required")
        return {"authority": "not_admitted", "models": [], "selectable": False}

    # This runner imports Request locally and uses deferred annotations.
    # Give FastAPI the actual Request class before registering this endpoint,
    # otherwise it can misclassify 'request' as a query parameter (HTTP 422).
    synthetic_observation.__annotations__["request"] = Request
    app.add_api_route(observation, synthetic_observation, methods=["GET"])

    client = TestClient(app)
    print("ASGI_SYNTHETIC_OBSERVATION_ROUTE_READY", flush=True)
    if (client.get(observation).status_code != 401
            or client.get(observation, headers={
                "Tailscale-User-Login": "synthetic-forged@example.invalid"
            }).status_code != 401):
        print("ASGI_UNSIGNED_NEGATIVE_FAIL", flush=True)
        raise RuntimeError("synthetic unsigned observation request admitted")
    print("ASGI_UNSIGNED_NEGATIVE_PASS", flush=True)
    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(
            lambda _: client.get(observation, headers=signed_headers).status_code,
            range(10),
        ))
    if codes.count(200) != 1 or codes.count(401) != 9:
        print("ASGI_CONCURRENT_STATUS_COUNTS",
              "accepted", codes.count(200), "denied", codes.count(401),
              "other", len(codes) - codes.count(200) - codes.count(401),
              flush=True)
        raise RuntimeError("real Redis ASGI ten-worker single-use gate failed")
    print("REAL_REDIS_ASGI_10_WORKER_SIGNED_OBSERVATION_PASS", flush=True)

    # Only the disposable container is restarted. Its keyspace is volatile.
    command("docker", "restart", "-t", "1", cid, timeout=18)
    assert_topology(cid, expected_image_id)
    for _ in range(20):
        try:
            new_run = redis.info()["run_id"].strip()
            if PIN.fullmatch(new_run) and new_run != pin:
                break
        except Exception:
            time.sleep(.1)
    else:
        raise RuntimeError("disposable Redis boot identity did not rotate")
    # Historic signed claim MUST NOT be reusable, even after Redis loses all
    # nonce keys. Never automatically authorize a new generation pin.
    if verified():
        raise RuntimeError("replay admitted after Redis lost its nonce data")
    if client.get(observation, headers=signed_headers).status_code != 401:
        raise RuntimeError("ASGI replay admitted across Redis restart")
    print("REAL_REDIS_RESTART_STALE_GATEWAY_ASSERTION_DENIED_PASS", flush=True)
    print("REAL_REDIS_ASGI_RESTART_REPLAY_DENIED_PASS", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--approve-disposable-replay", action="store_true")
    ap.add_argument("--expected-source-sha", required=True)
    ap.add_argument("--redis-image-id", required=True)
    args = ap.parse_args(argv)
    if not (
        args.approve_disposable_replay
        and isinstance(args.expected_source_sha, str)
        and PIN.fullmatch(args.expected_source_sha)
        and isinstance(args.redis_image_id, str)
        and DIGEST.fullmatch(args.redis_image_id)
    ):
        print("HOLD: exact source/image identity and explicit approval required")
        return 2
    try:
        if command("git", "-C", str(ROOT), "rev-parse", "HEAD") != args.expected_source_sha:
            raise RuntimeError("source head mismatch")
        if command("git", "-C", str(ROOT), "status", "--porcelain"):
            raise RuntimeError("source worktree dirty")
        if command("docker", "image", "inspect", IMAGE,
                   "--format", "{{.Id}}") != args.redis_image_id:
            raise RuntimeError("image digest mismatch")
        exists = subprocess.run(
            ["docker", "inspect", "--type", "container", NAME],
            capture_output=True, timeout=8, check=False,
        )
        if exists.returncode == 0:
            raise RuntimeError("refusing to touch pre-existing container")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        print("HOLD: preflight not verified")
        return 2

    owned_id: str | None = None
    try:
        owned_id = command(
            "docker", "run", "-d", "--rm", "--pull", "never", "--name", NAME,
            "--network", "none", "--read-only",
            "--tmpfs", "/data:rw,size=16m,mode=1777",
            "--tmpfs", "/tmp:rw,size=8m,mode=1777",
            "--user", "999:999", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--cpus", "0.25", "--memory", "128m", "--pids-limit", "32",
            args.redis_image_id, "redis-server", "--save", "", "--appendonly", "no",
        )
        if not CID.fullmatch(owned_id):
            raise RuntimeError("unidentified disposable Redis")
        assert_topology(owned_id, args.redis_image_id)
        trial(owned_id, args.redis_image_id)
        return 0
    except (OSError, RuntimeError, ValueError, KeyError,
            subprocess.SubprocessError):
        print("DISPOSABLE_GATEWAY_REPLAY_TRIAL_FAIL_OR_INCONCLUSIVE")
        return 1
    finally:
        if owned_id is not None and CID.fullmatch(owned_id):
            subprocess.run(
                ["docker", "stop", "-t", "2", owned_id],
                capture_output=True, timeout=10, check=False,
            )


if __name__ == "__main__":
    raise SystemExit(main())
