#!/usr/bin/env python3
"""Explicit one-shot physical API-container→HMAC→host SQLite slot→LFM3 probe.

NEVER run by cron, CI or production. Creates only a disposable synthetic grant,
uses an existing running assistx-api container and host-loopback model, never
calls Task or Neo4j APIs. No owner signatures or live slots are authorized.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib import error, request

from fleet_lfm3_signed_slot_bridge_canary import sign
from fleet_resource_slot_local_staging_witness import provision_disposable_synthetic_db

BRIDGE = Path(__file__).with_name("fleet_lfm3_signed_slot_bridge_canary.py")
APPROVED_CONTAINER = "assistx-api"
PORT = 19203

# This source is sent to a preexisting container as a one-shot *read-only*
# HTTP client. The ephemeral HMAC secret is passed over stdin, never via
# environment, docker command args, disk or logs. No container image changes.
CONTAINER_PROBE = r"""
import hashlib,hmac,json,os,sys,time
from urllib import request,error
secret=bytes.fromhex(sys.stdin.read().strip())
assert len(secret)==32
payload={"model":"lfm3-1.7b-x1-fast","messages":[{"role":"user","content":"Reply OK."}],"max_tokens":4,"temperature":0,"stream":False}
body=json.dumps(payload,separators=(',',':')).encode()
stamp=str(int(time.time()))
nonce=os.urandom(16).hex()
sha=hashlib.sha256(body).hexdigest()
canonical=f"POST\n/staging-canary\n{stamp}\n{nonce}\n{sha}".encode()
signed=hmac.new(secret,canonical,hashlib.sha256).hexdigest()
url="http://172.21.0.1:19203/staging-canary"
base={"Content-Type":"application/json","X-Canary-Date":stamp,"X-Canary-Nonce":nonce}
client=request.build_opener(request.ProxyHandler({}))
def send(sig):
    req=request.Request(url,data=body,headers={**base,"X-Canary-Signature":sig},method="POST")
    try:
        with client.open(req,timeout=8) as response:
            return response.status,json.load(response)
    except error.HTTPError as response:
        return response.code,json.load(response)
invalid=send("0"*64)
good=send(signed)
replay=send(signed)
assert invalid[0]==403 and good[0]==200 and replay[0]==403
assert good[1]["task_admission"]=="HOLD" and good[1]["completion_tokens"] in (1,2,3,4)
assert good[1]["host_slot_fenced_at_accept_and_result"] is True
assert good[1]["approved_parallel_slots"]==0 and not good[1]["owner_signed"]
print(json.dumps({"bad_hmac_denied":True,"signed_fixed_model_http":good[0],
                  "completion_tokens":good[1]["completion_tokens"],"replayed_nonce_denied":True,
                  "task_admission":"HOLD","approved_parallel_slots":0}))
"""


def submit_host_wrong_source_request(bind_ip: str, secret: bytes) -> int:
    # Client source is the host, not the approved Docker container; a genuine
    # signature still must never bypass the source restriction.
    body = b'{"node":"wrong-source"}'
    stamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    req = request.Request(
        f"http://{bind_ip}:{PORT}/staging-canary",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Canary-Date": stamp,
            "X-Canary-Nonce": nonce,
            "X-Canary-Signature": sign(secret, stamp, nonce, body),
        },
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=4) as resp:
            return resp.status
    except error.HTTPError as resp:
        return resp.code


def run(bind_ip: str, container_ip: str) -> dict:
    if os.geteuid() == 0:
        raise ValueError("staging_probe_rejects_root")
    # This reproducible physical harness is pinned to the observed API bridge.
    # A changed Docker network needs independent inspection before rerun.
    if (bind_ip, container_ip) != ("172.21.0.1", "172.21.0.6"):
        raise ValueError("physical_probe_network_must_be_reverified")
    # NO persistent gateway; synthetic fixture erased even after failure.
    with tempfile.TemporaryDirectory(prefix="lfm3-signed-slot-bridge-stage-") as location:
        private = Path(location)
        private.chmod(0o700)
        ledger = private / "synthetic-slot-ledger.sqlite"
        provision_disposable_synthetic_db(ledger, now_ms=int(time.time() * 1000))
        secret = secrets.token_bytes(32)
        rd, wr = os.pipe()
        server = None
        try:
            server = subprocess.Popen(
                [
                    sys.executable,
                    str(BRIDGE),
                    "--staging",
                    "--listen-ip",
                    bind_ip,
                    "--client-ip",
                    container_ip,
                    "--port",
                    str(PORT),
                    "--secret-fd",
                    str(rd),
                    "--ledger-path",
                    str(ledger),
                    "--max-seconds",
                    "14",
                    "--max-requests",
                    "4",
                ],
                pass_fds=(rd,),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                start_new_session=True,
            )
            os.close(rd)
            rd = -1
            os.write(wr, secret)
            os.close(wr)
            wr = -1
            line = server.stdout.readline().strip()
            if not line or json.loads(line).get("status") != "SLOT_HMAC_STAGE_READY":
                raise ValueError("staging_bridge_failed_closed")
            wrong_source = submit_host_wrong_source_request(bind_ip, secret)
            if wrong_source != 403:
                raise ValueError("signed_unapproved_source_was_not_denied")
            probe = subprocess.run(
                ["docker", "exec", "-i", APPROVED_CONTAINER, "python3", "-c", CONTAINER_PROBE],
                input=secret.hex(),
                text=True,
                capture_output=True,
                timeout=15,
            )
            if probe.returncode != 0:
                raise ValueError("container_signed_staging_probe_failed")
            observation = json.loads(probe.stdout.strip())
            finished, errors = server.communicate(timeout=8)
            if server.returncode != 2:
                raise ValueError("gateway_did_not_fail_closed")
            if '"status": "CLOSED"' not in finished:
                raise ValueError("gateway_socket_teardown_not_confirmed")
            with socket.socket() as sock:
                sock.settimeout(1)
                if sock.connect_ex((bind_ip, PORT)) == 0:
                    raise ValueError("private_gateway_still_listening")
            if not all(
                (
                    observation["bad_hmac_denied"],
                    observation["replayed_nonce_denied"],
                    observation["task_admission"] == "HOLD",
                )
            ):
                raise ValueError("invalid_staging_observation")
            return {
                "physical_stage": "PASS",
                "host_unapproved_signed_source_denied": True,
                **observation,
                "ephemeral_ledger_deleted_after_probe": True,
                "owner_signed": False,
                "model_upstream_exclusivity_proven": False,
                "production_inference_authorized": False,
            }
        finally:
            if rd >= 0:
                os.close(rd)
            if wr >= 0:
                os.close(wr)
            if server is not None and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=3)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true")
    p.add_argument("--listen-ip")
    p.add_argument("--client-ip")
    args = p.parse_args(argv)
    if not args.live:
        print(json.dumps({"status": "HOLD", "reason": "EXPLICIT_OPERATOR_STAGING_ONLY", "task_admission": "HOLD"}))
        return 2
    try:
        if args.listen_ip is None or args.client_ip is None:
            raise ValueError("staging_client_and_bind_required")
        evidence = run(args.listen_ip, args.client_ip)
        print(json.dumps(evidence, sort_keys=True))
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError):
        print(json.dumps({"physical_stage": "HOLD", "task_admission": "HOLD", "reason": "PHYSICAL_STAGING_NOT_PROVEN"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
