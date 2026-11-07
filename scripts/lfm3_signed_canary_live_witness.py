#!/usr/bin/env python3
"""One-shot physical signed canary via production API Docker CLIENT, no Task API.

All credentials are random ephemeral test-only bytes through anonymous pipes,
never environment, disk, command arguments or logs. API container receives
only the key on stdin and can issue exactly one fixed synthetic prompt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import select
import socket
import subprocess
import sys
import time
from urllib import error, request

from lfm3_signed_private_canary import private_docker_bind

CONTAINER = "assistx-api"
NETWORK = "home_scott_git_auto-assist_default"
GATEWAY_PORT = 19183

# Source is fixed literal code supplied as the docker exec command argument.
# Secrets travel over that child process's stdin, never in Python code/env/argv.
CLIENT_CODE = r"""
import hashlib,hmac,json,secrets,sys,time,urllib.request,urllib.error
key=sys.stdin.buffer.read(33)
assert len(key)==32
base='http://172.21.0.1:19183'
body=json.dumps({"model":"lfm3-1.7b-x1-fast","messages":[{"role":"user","content":"Reply OK."}],"max_tokens":4,"temperature":0,"stream":False}).encode()
ts=str(int(time.time())); nonce=secrets.token_hex(16)
digest=hashlib.sha256(body).hexdigest()
msg=f'POST\n/canary\n{ts}\n{nonce}\n{digest}'.encode('ascii')
sig=hmac.new(key,msg,hashlib.sha256).hexdigest()
client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def hit(signature, expected):
 req=urllib.request.Request(base+'/canary',data=body,method='POST',headers={
 'Content-Type':'application/json','X-Canary-Date':ts,'X-Canary-Nonce':nonce,
 'X-Canary-Signature':signature})
 try:
  with client.open(req,timeout=8) as r: code=r.status; reply=json.load(r)
 except urllib.error.HTTPError as e:code=e.code;reply=json.load(e)
 assert code==expected,(code,expected)
 assert reply['task_admission']=='HOLD'
 print('API_DOCKER_CANARY_HTTP='+str(code)+' ADMISSION=HOLD')
 return reply
hit('0'*64,403)
positive=hit(sig,200)
assert positive['contract']=='lfm3-private-signed-canary-v1'
assert positive['model_alias']=='lfm3-1.7b-x1-fast'
assert positive['upstream_http']==200
assert 1<=positive['completion_tokens']<=4
assert positive['production_inference_authorized'] is False
assert positive['resource_slots_approved']==0
hit(sig,409)
print('SIGNED_LFM3_PHYSICAL_CANARY=PASS; successful_generations=1')
print('prompt_or_model_output_not_emitted=YES; approved_tasks=0')
"""


def docker_private_addresses() -> tuple[str, str]:
    inspect = subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, text=True, check=True, timeout=5)
    containers = json.loads(inspect.stdout)
    if not isinstance(containers, list) or len(containers) != 1:
        raise ValueError("container_identity_unverified")
    container = containers[0]
    if container.get("State", {}).get("Running") is not True:
        raise ValueError("api_not_running")
    networks = container.get("NetworkSettings", {}).get("Networks", {})
    net = networks.get(NETWORK, {})
    source = net.get("IPAddress")
    gateway = net.get("Gateway")
    if not isinstance(source, str) or not isinstance(gateway, str):
        raise ValueError("docker_network_identity_unresolved")
    iface = subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True, check=True, timeout=3)
    private_docker_bind(gateway, source, json.loads(iface.stdout))
    if source != "172.21.0.6" or gateway != "172.21.0.1":
        # Fixed live client code is deliberately not a dynamic host router.
        raise ValueError("physical_network_changed_review_required")
    return source, gateway


def verify_local_lfm3_idle() -> None:
    opener = request.build_opener(request.ProxyHandler({}))
    for route in ("/health", "/slots"):
        with opener.open(f"http://127.0.0.1:19181{route}", timeout=3) as response:
            if response.status != 200:
                raise ValueError("lfm3_not_ready")
            payload = json.loads(response.read(16 * 1024))
        if route == "/health" and payload.get("status") != "ok":
            raise ValueError("lfm3_health_not_ok")
        if route == "/slots" and (
            not isinstance(payload, list) or len(payload) != 1 or payload[0].get("is_processing") is not False
        ):
            raise ValueError("no_independent_idle_slot")


def host_wrong_source_denial(gateway: str) -> None:
    data = b"{}"
    req = request.Request(
        f"http://{gateway}:{GATEWAY_PORT}/canary",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.build_opener(request.ProxyHandler({})).open(req, timeout=3) as reply:
            code = reply.status
    except error.HTTPError as ex:
        code = ex.code
    if code != 403:
        raise ValueError("host_wrong_source_not_denied")
    print("HOST_WRONG_SOURCE_HTTP=403", flush=True)


def run() -> int:
    if os.geteuid() == 0:
        raise ValueError("root_orchestrator_forbidden")
    source, gateway = docker_private_addresses()
    verify_local_lfm3_idle()
    with socket.socket() as probe:
        probe.settimeout(0.15)
        if probe.connect_ex((gateway, GATEWAY_PORT)) == 0:
            raise ValueError("port_collision_fail_closed")
    key = secrets.token_bytes(32)
    read_fd, write_fd = os.pipe()
    server_file = Path(__file__).with_name("lfm3_signed_private_canary.py")
    cmd = [
        sys.executable,
        "-u",
        str(server_file),
        "--listen-ip",
        gateway,
        "--allowed-client",
        source,
        "--port",
        str(GATEWAY_PORT),
        "--token-fd",
        str(read_fd),
        "--max-requests",
        "4",
    ]
    server = subprocess.Popen(
        cmd, pass_fds=(read_fd,), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    os.close(read_fd)
    try:
        os.write(write_fd, key)
    finally:
        os.close(write_fd)
    try:
        readable, _, _ = select.select([server.stdout], [], [], 5)
        if not readable:
            raise ValueError("server_not_ready")
        line = server.stdout.readline()
        status = json.loads(line)
        if status.get("status") != "SIGNED_CANARY_READY":
            raise ValueError("server_failed_closed")
        print("PRIVATE_DOCKER_SIGNED_GATE=READY; task_admission=HOLD", flush=True)
        host_wrong_source_denial(gateway)
        client = subprocess.run(
            ["docker", "exec", "-i", CONTAINER, "python3", "-c", CLIENT_CODE],
            input=key,
            capture_output=True,
            timeout=14,
        )
        if client.returncode != 0:
            raise ValueError("api_container_canary_failed")
        lines = client.stdout.decode("utf8", "replace").splitlines()
        expected = [
            "API_DOCKER_CANARY_HTTP=403 ADMISSION=HOLD",
            "API_DOCKER_CANARY_HTTP=200 ADMISSION=HOLD",
            "API_DOCKER_CANARY_HTTP=409 ADMISSION=HOLD",
            "SIGNED_LFM3_PHYSICAL_CANARY=PASS; successful_generations=1",
            "prompt_or_model_output_not_emitted=YES; approved_tasks=0",
        ]
        if lines != expected:
            raise ValueError("unexpected_canary_output_contract")
        for safe in lines:
            print(safe, flush=True)
        code = server.wait(timeout=8)
        if code != 2:
            raise ValueError("server_did_not_exit_hold")
        completed = server.stdout.read().strip()
        if json.loads(completed).get("status") != "CLOSED":
            raise ValueError("server_not_closed")
        with socket.socket() as probe:
            probe.settimeout(0.15)
            if probe.connect_ex((gateway, GATEWAY_PORT)) == 0:
                raise ValueError("listener_remained_open")
        print("EPHEMERAL_SIGNED_GATE_CLOSED=PASS; live_TASK_ADMISSION=HOLD", flush=True)
        return 2
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=2)


def main() -> int:
    try:
        return run()
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError):
        print("SIGNED_PHYSICAL_WITNESS=HOLD; no_approval_or_task_authority", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
