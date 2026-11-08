#!/usr/bin/env python3
"""One-off dual-container namespace isolation + HMAC slot LFM3 witness.

Default is NO ACTION/HOLD. Explicit --live creates two short-lived unprivileged
containers; a separate model bound to its OWN localhost and a slot-fencing
HMAC gateway SHARING its namespace. No published ports; only fixed benign
model canary; no owner signatures, Tasks, production changes or persistent DB.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import select
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib import error, request

from fleet_lfm3_signed_slot_bridge_canary import sign
from fleet_resource_slot_local_staging_witness import provision_disposable_synthetic_db

API_CONTAINER = "assistx-api"
NETWORK = "home_scott_git_auto-assist_default"
IMAGE = "auto-assist-wt-ab3bb64-api:latest"
MODEL_CONTAINER = "lfm3-namespace-stage-model-20261108"
GATEWAY_CONTAINER = "lfm3-namespace-stage-gateway-20261108"
PORT = 19205
MODEL_PORT = 19185
MODEL_NAME = "lfm3-isolated-namespace-poc"
MODEL_BINARY = "/home/scott/.local/share/kipnerter-lfm3-fast/runtime-vulkan-v0.7.0-3fd5de70"
MODEL_LIBS = "/home/scott/lfm3-research/llama-build/bin"
MODEL_FILE = "/home/scott/models/LFM3-1.7B-Q4_K_M.gguf"
REPO = Path(__file__).resolve().parents[1]

# Client executes inside existing API container; stdin carries ONLY an ephemeral
# HMAC key. The caller never receives ledger, slot token or upstream model port.
CLIENT = r"""
import hashlib,hmac,json,os,socket,sys,time
from urllib import request,error
ip=sys.argv[1]
key=bytes.fromhex(sys.stdin.read().strip())
assert len(key)==32
with socket.socket() as direct:
 direct.settimeout(0.6)
 assert direct.connect_ex((ip,19185))!=0
body=json.dumps({"model":"lfm3-1.7b-x1-fast","messages":[{"role":"user","content":"Reply OK."}],"max_tokens":4,"temperature":0,"stream":False},separators=(',',':')).encode()
now=str(int(time.time()))
nonce=os.urandom(16).hex()
digest=hashlib.sha256(body).hexdigest()
message=f"POST\n/staging-canary\n{now}\n{nonce}\n{digest}".encode()
signed=hmac.new(key,message,hashlib.sha256).hexdigest()
url=f"http://{ip}:19205/staging-canary"
headers={"Content-Type":"application/json","X-Canary-Date":now,"X-Canary-Nonce":nonce}
client=request.build_opener(request.ProxyHandler({}))
def post(sig):
 req=request.Request(url,method="POST",data=body,headers={**headers,"X-Canary-Signature":sig})
 try:
  with client.open(req,timeout=8) as r: return r.status,json.load(r)
 except error.HTTPError as r: return r.code,json.load(r)
wrong=post("0"*64)
good=post(signed)
replay=post(signed)
assert wrong[0]==403 and good[0]==200 and replay[0]==403
assert good[1]["completion_tokens"] in (1,2,3,4) and good[1]["task_admission"]=="HOLD"
assert good[1]["host_slot_fenced_at_accept_and_result"] is True
assert good[1]["approved_parallel_slots"]==0 and not good[1]["owner_signed"]
print(json.dumps({"model_direct_network_denied":True,"bad_hmac_denied":True,"gateway_http":good[0],
                  "completion_tokens":good[1]["completion_tokens"],"replay_denied":True,
                  "approved_parallel_slots":0,"task_admission":"HOLD"}))
"""


def docker(*args: str, timeout: float = 10.0) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise ValueError("docker_stage_operation_failed")
    return done.stdout.strip()


def safe_ip(text: str) -> str:
    try:
        addr = ipaddress.ip_address(text)
        if addr.version != 4 or not addr.is_private or addr.is_loopback:
            raise ValueError("not_private_ipv4")
        return str(addr)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid_private_stage_ip") from exc


def approved_api_ip() -> str:
    attached = json.loads(docker("inspect", API_CONTAINER, "--format", "{{json .NetworkSettings.Networks}}"))
    if not isinstance(attached, dict) or NETWORK not in attached:
        raise ValueError("approved_api_network_not_present")
    return safe_ip(attached[NETWORK]["IPAddress"])


def model_ip() -> str:
    attached = json.loads(docker("inspect", MODEL_CONTAINER, "--format", "{{json .NetworkSettings.Networks}}"))
    if not isinstance(attached, dict) or list(attached) != [NETWORK]:
        raise ValueError("model_network_identity_does_not_match")
    return safe_ip(attached[NETWORK]["IPAddress"])


def valid_no_direct_model(ip: str) -> None:
    with socket.socket() as sock:
        sock.settimeout(0.7)
        if sock.connect_ex((ip, MODEL_PORT)) == 0:
            raise ValueError("upstream_publicly_reachable_bypass")


def host_wrong_source_denied(ip: str, secret: bytes) -> bool:
    body = b"{}"
    nonce = secrets.token_hex(16)
    timestamp = str(int(time.time()))
    req = request.Request(
        f"http://{ip}:{PORT}/staging-canary",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Canary-Date": timestamp,
            "X-Canary-Nonce": nonce,
            "X-Canary-Signature": sign(secret, timestamp, nonce, body),
        },
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=3) as resp:
            return resp.status == 403
    except error.HTTPError as resp:
        return resp.code == 403


def cleanup():
    for name in (GATEWAY_CONTAINER, MODEL_CONTAINER):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=5)


def run() -> dict:
    if os.geteuid() == 0:
        raise ValueError("nonroot_staging_operator_only")
    # Distinct named containers prevent accidental reuse of an existing fleet
    # service or silently starting under unreviewed network aliases.
    existing = docker("ps", "-a", "--format", "{{.Names}}", timeout=6).splitlines()
    if MODEL_CONTAINER in existing or GATEWAY_CONTAINER in existing:
        raise ValueError("staging_container_name_already_occupied")
    if NETWORK not in docker("network", "ls", "--format", "{{.Name}}", timeout=6).splitlines():
        raise ValueError("approved_network_absent")
    if IMAGE not in docker("images", "--format", "{{.Repository}}:{{.Tag}}", timeout=6).splitlines():
        raise ValueError("fixed_local_image_missing")
    client_ip = approved_api_ip()
    with tempfile.TemporaryDirectory(prefix="lfm3-coisolated-slot-stage-") as temporary:
        private = Path(temporary)
        private.chmod(0o700)
        ledger = private / "staging-synthetic-slot.sqlite"
        provision_disposable_synthetic_db(ledger, now_ms=int(time.time() * 1000))
        stage = None
        try:
            # Fixed CPU-only LFM3 in a fresh private container loopback, not
            # the existing host model. Zero host port mappings or credentials.
            docker(
                "run",
                "--pull",
                "never",
                "-d",
                "--rm",
                "--name",
                MODEL_CONTAINER,
                "--network",
                NETWORK,
                "--read-only",
                "--user",
                "1000:1000",
                "--memory",
                "3g",
                "--cpus",
                "3",
                "--pids-limit",
                "96",
                "--security-opt",
                "no-new-privileges",
                "--cap-drop",
                "ALL",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=96m",
                "-v",
                MODEL_BINARY + ":/opt/lfm3-runtime:ro",
                "-v",
                MODEL_LIBS + ":" + MODEL_LIBS + ":ro",
                "-v",
                MODEL_FILE + ":/opt/lfm3.gguf:ro",
                "--entrypoint",
                "/opt/lfm3-runtime/bin/llama-server",
                IMAGE,
                "--model",
                "/opt/lfm3.gguf",
                "--alias",
                MODEL_NAME,
                "--host",
                "127.0.0.1",
                "--port",
                str(MODEL_PORT),
                "--threads",
                "3",
                "--parallel",
                "1",
                "--ctx-size",
                "1024",
                "--batch-size",
                "128",
                "--n-gpu-layers",
                "0",
                "--no-webui",
                timeout=14,
            )
            ip = model_ip()
            if ip == client_ip:
                raise ValueError("inference_model_ip_equals_approved_api_ip")
            attached = json.loads(docker("inspect", MODEL_CONTAINER, "--format", "{{json .HostConfig.PortBindings}}"))
            if attached not in ({}, None):
                raise ValueError("model_published_any_port")
            valid_no_direct_model(ip)
            ready = False
            for _ in range(25):
                try:
                    docker(
                        "exec",
                        MODEL_CONTAINER,
                        "python3",
                        "-c",
                        "import urllib.request,json; assert json.load(urllib.request.urlopen('http://127.0.0.1:19185/health',timeout=1))['status']=='ok'",
                        timeout=3,
                    )
                    ready = True
                    break
                except (ValueError, subprocess.TimeoutExpired):
                    time.sleep(0.7)
            if not ready:
                raise ValueError("namespaced_model_not_ready")
            valid_no_direct_model(ip)
            secret = secrets.token_bytes(32)
            cmd = [
                "docker",
                "run",
                "--pull",
                "never",
                "--rm",
                "-i",
                "--name",
                GATEWAY_CONTAINER,
                "--network",
                "container:" + MODEL_CONTAINER,
                "--read-only",
                "--user",
                "1000:1000",
                "--memory",
                "1g",
                "--cpus",
                "2",
                "--pids-limit",
                "96",
                "--security-opt",
                "no-new-privileges",
                "--cap-drop",
                "ALL",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=48m",
                "-v",
                str(REPO) + ":/work:ro",
                "-v",
                str(private) + ":/staging:rw",
                "--entrypoint",
                "python3",
                IMAGE,
                "/work/scripts/fleet_lfm3_namespaced_slot_gateway.py",
                "--staging",
                "--bind-ip",
                ip,
                "--client-ip",
                client_ip,
                "--ledger-path",
                "/staging/staging-synthetic-slot.sqlite",
                "--max-seconds",
                "14",
                "--max-requests",
                "4",
            ]
            stage = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert stage.stdin and stage.stdout
            stage.stdin.write(secret)
            stage.stdin.close()
            readable, _, _ = select.select([stage.stdout], [], [], 9)
            if not readable:
                raise ValueError("namespaced_gateway_readiness_timeout")
            line = stage.stdout.readline().decode().strip()
            if not line or json.loads(line).get("status") != "ISOLATED_MODEL_SLOT_GATEWAY_READY":
                raise ValueError("namespaced_gateway_failed_closed")
            # Do not rely solely on implicit Docker IPC namespacing: verify.
            actual_mode = docker("inspect", GATEWAY_CONTAINER, "--format", "{{.HostConfig.NetworkMode}}")
            if actual_mode != "container:" + MODEL_CONTAINER:
                # Docker may normalize to container ID; same model ID is OK.
                if not actual_mode.startswith("container:"):
                    raise ValueError("gateway_not_in_model_namespace")
                model_cid = docker("inspect", MODEL_CONTAINER, "--format", "{{.Id}}")
                if actual_mode != "container:" + model_cid:
                    raise ValueError("gateway_namespaced_to_wrong_container")
            valid_no_direct_model(ip)
            if not host_wrong_source_denied(ip, secret):
                raise ValueError("host_source_with_valid_hmac_not_denied")
            client = subprocess.run(
                ["docker", "exec", "-i", API_CONTAINER, "python3", "-c", CLIENT, ip],
                input=secret.hex(),
                capture_output=True,
                text=True,
                timeout=14,
            )
            if client.returncode != 0:
                raise ValueError("api_to_gateway_private_canary_failed")
            observation = json.loads(client.stdout.strip())
            if (
                observation["gateway_http"] != 200
                or not observation["bad_hmac_denied"]
                or not observation["replay_denied"]
                or not observation["model_direct_network_denied"]
                or observation["task_admission"] != "HOLD"
            ):
                raise ValueError("namespaced_staging_evidence_invalid")
            # Bounded gateway auto-exits after 4 requests; any leaked server
            # or failure to close is a hard HOLD.
            rc = stage.wait(timeout=7)
            if rc != 2:
                raise ValueError("staging_gateway_did_not_return_hold_exit")
            output = stage.stdout.read(512).decode()
            if '"status": "CLOSED"' not in output:
                raise ValueError("namespace_gateway_close_not_witnessed")
            valid_no_direct_model(ip)
            return {
                "physical_stage": "PASS",
                "host_direct_model_denied": True,
                "model_container_network_private": True,
                "gateway_shares_model_namespace": True,
                "host_wrong_source_with_valid_hmac_denied": True,
                "gateway_listener_closed": True,
                **observation,
                "synthetic_ledger_deleted_after_stage": True,
                "owner_signed": False,
                "approved_parallel_slots": 0,
                "production_task_authorized": False,
            }
        finally:
            if stage is not None and stage.poll() is None:
                stage.terminate()
                try:
                    stage.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    stage.kill()
                    stage.wait(timeout=3)
            cleanup()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true")
    args = p.parse_args(argv)
    if not args.live:
        print(json.dumps({"status": "HOLD", "reason": "EXPLICIT_OPERATOR_ONLY", "task_admission": "HOLD"}))
        return 2
    try:
        result = run()
        print(json.dumps(result, sort_keys=True))
    except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
        print(json.dumps({"physical_stage": "HOLD", "task_admission": "HOLD", "reason": "NAMESPACE_GATE_NOT_PROVEN"}))
    return 2  # never a production authorization


if __name__ == "__main__":
    raise SystemExit(main())
