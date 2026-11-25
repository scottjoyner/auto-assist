#!/usr/bin/env python3
"""Ephemeral non-admitting native llama.cpp Unix-socket + API-key witness.

Runs one private CPU-only staging llama-server process, without TCP ingress.
Authenticates one pre-existing LFM3 GGUF through a memfd-only random test key
and a no-network Docker client via a mounted owner-private Unix socket.
No signer, production Task, graph, agent, routing, or persisted service changes.
Exit status 2/HOLD regardless of a successful physical inference test.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import resource
import secrets
import socket
import subprocess
import tempfile
import time
from typing import Any

EXPECTED_MODEL_BYTES = 1099627808
EXPECTED_MODEL_SHA256 = "3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4"
EXPECTED_SOURCE_PORTS = ("19180", "19181")
CONTAINER_IMAGE = "auto-assist-wt-ab3bb64-api:latest"
SOCKET_NAME = "api.sock"
MAX_TOKENS = 32


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, address: str):
        super().__init__("localhost", timeout=8)
        self.address = address

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(8)
        self.sock.connect(self.address)


def unix_request(
    socket_path: str, route: str, *, payload: dict | None = None, key: str | None = None
) -> tuple[int, dict[str, Any]]:
    if route not in ("/health", "/v1/models", "/v1/chat/completions"):
        raise ValueError("unapproved_native_socket_route")
    if (route == "/v1/chat/completions") != (payload is not None):
        raise ValueError("unapproved_native_socket_method")
    head = {"Content-Type": "application/json"}
    if key is not None:
        head["Authorization"] = "Bearer " + key
    client = UnixConnection(socket_path)
    try:
        client.request(
            "POST" if payload is not None else "GET",
            route,
            body=json.dumps(payload).encode() if payload is not None else None,
            headers=head,
        )
        response = client.getresponse()
        status = response.status
        raw = response.read(64 * 1024 + 1)
    finally:
        client.close()
    if len(raw) > 64 * 1024:
        raise ValueError("native_socket_response_oversized")
    try:
        value = json.loads(raw)
    except ValueError:
        value = {}
    return status, value if isinstance(value, dict) else {}


def validate_generation(status: int, obj: Any) -> int:
    if status != 200 or not isinstance(obj, dict):
        raise ValueError("native_inference_not_200")
    choices = obj.get("choices")
    usage = obj.get("usage")
    if (
        not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], dict)
        or not isinstance(usage, dict)
    ):
        raise ValueError("native_inference_response_malformed")
    msg = choices[0].get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    tokens = usage.get("completion_tokens")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("native_inference_no_final_content")
    if type(tokens) is not int or not 1 <= tokens <= MAX_TOKENS:
        raise ValueError("native_inference_token_budget_invalid")
    if choices[0].get("finish_reason") != "stop":
        raise ValueError("native_inference_incomplete_finish")
    return tokens


def fixed_prompt(model_id: str) -> dict[str, Any]:
    if not isinstance(model_id, str) or not model_id or len(model_id) > 512:
        raise ValueError("invalid_native_model_id")
    return {
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply OK."}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "stream": False,
    }


def server_command(binary: str, model: str, socket_path: str, fd: int) -> list[str]:
    if not (socket_path.endswith(".sock") and socket_path.startswith("/")) or fd < 3:
        raise ValueError("invalid_native_socket_or_fd")
    return [
        binary,
        "--model",
        model,
        "--host",
        socket_path,
        "--ctx-size",
        "512",
        "--parallel",
        "1",
        "--threads",
        "2",
        "--n-gpu-layers",
        "0",
        "--no-webui",
        "--api-key-file",
        f"/proc/self/fd/{fd}",
        "--reasoning-budget",
        "0",
    ]


def docker_command(private_stage_dir: str, uid: int, gid: int) -> list[str]:
    if not private_stage_dir.startswith("/") or ":" in private_stage_dir:
        raise ValueError("invalid_private_stage_path")
    return [
        "docker",
        "run",
        "-i",
        "--pull",
        "never",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--user",
        f"{uid}:{gid}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "768m",
        "--pids-limit",
        "64",
        "-v",
        private_stage_dir + ":/run/lfm3:ro",
        "--entrypoint",
        "python",
        CONTAINER_IMAGE,
        "-c",
        DOCKER_CLIENT_CODE,
    ]


DOCKER_CLIENT_CODE = r"""
import sys,socket,http.client,json
key=sys.stdin.buffer.read(128).strip().decode();assert 20 < len(key) < 110
class Conn(http.client.HTTPConnection):
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
  self.sock.settimeout(8);self.sock.connect('/run/lfm3/api.sock')
def hit(route,body=None,secret=None):
 c=Conn('localhost',timeout=8);head={'Content-Type':'application/json'}
 if secret:head['Authorization']='Bearer '+secret
 c.request('POST' if body else 'GET',route,
   body=json.dumps(body).encode() if body else None,headers=head)
 r=c.getresponse();status=r.status;raw=r.read(65536);c.close()
 try:obj=json.loads(raw)
 except ValueError:obj={}
 return status,obj
catalog_status,catalog=hit('/v1/models',secret=key)
assert catalog_status==200 and len(catalog.get('data',[]))==1
model_id=catalog['data'][0]['id']
body={'model':model_id,'messages':[{'role':'user','content':'Reply OK.'}],
      'max_tokens':32,'temperature':0,'stream':False}
unauth_status,_=hit('/v1/chat/completions',body)
assert unauth_status in (401,403)
status,obj=hit('/v1/chat/completions',body,key)
assert status==200 and len(obj.get('choices',[]))==1
msg=obj['choices'][0].get('message',{});out=msg.get('content')
tokens=obj.get('usage',{}).get('completion_tokens')
assert isinstance(out,str) and bool(out.strip())
assert type(tokens) is int and 1<=tokens<=32
assert obj['choices'][0].get('finish_reason')=='stop'
print('unix_native_missing_key_http='+str(unauth_status))
print('unix_native_authorized_http='+str(status)+' tokens='+str(tokens)+' nonblank_final=True')
print('network_none_unix_socket_auth=PASS; task_admission=HOLD')
"""


def physical_source() -> tuple[str, str, dict[str, int]]:
    process_ids: dict[str, int] = {}
    exe = model = None
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = [item.decode("utf8", "replace") for item in (proc / "cmdline").read_bytes().split(b"\x00") if item]
            if not argv or Path(argv[0]).name != "llama-server" or "--port" not in argv:
                continue
            port = argv[argv.index("--port") + 1]
            if port not in EXPECTED_SOURCE_PORTS:
                continue
            process_ids[port] = int(proc.name)
            if port == "19180":
                flag = "--model" if "--model" in argv else "-m"
                exe = argv[0]
                model = argv[argv.index(flag) + 1]
        except (OSError, IndexError, ValueError):
            continue
    if len(process_ids) != 2 or not exe or not model or not os.access(exe, os.X_OK):
        raise ValueError("source_runtime_identity_unverified")
    item = Path(model)
    if item.is_symlink() or not item.is_file() or item.stat().st_size != EXPECTED_MODEL_BYTES:
        raise ValueError("source_artifact_identity_unverified")
    before = item.stat()
    with item.open("rb") as source:
        sha = hashlib.file_digest(source, "sha256").hexdigest()
    after = item.stat()

    def signature(x):
        return (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns)

    if sha != EXPECTED_MODEL_SHA256 or signature(before) != signature(after):
        raise ValueError("source_artifact_fingerprint_mismatch")
    return exe, str(item), process_ids


def _limit_stage() -> None:
    os.nice(10)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    cap = 5 * 1024**3
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def run_live() -> dict[str, Any]:
    if os.geteuid() == 0:
        raise ValueError("root_stage_forbidden")
    source, model, original_pids = physical_source()
    available = 0
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            available = int(line.split()[1]) * 1024
            break
    if available < 8 * 1024**3:
        raise ValueError("insufficient_free_host_memory")
    psi = Path("/proc/pressure/memory").read_text().splitlines()[0]
    if float(psi.split("avg10=")[1].split()[0]) >= 0.2:
        raise ValueError("host_memory_pressure_high")
    print("native_source_model_fingerprint=PASS; stage_permissions=NONROOT", flush=True)
    with tempfile.TemporaryDirectory(
        prefix="lfm3-native-api-canary-", dir=str(Path.home() / ".local/state")
    ) as private_dir:
        os.chmod(private_dir, 0o700)
        sock = str(Path(private_dir) / SOCKET_NAME)
        token = secrets.token_urlsafe(32)
        fd = os.memfd_create("synthetic-one-shot-native-api-key", flags=0)
        os.write(fd, (token + "\n").encode())
        os.lseek(fd, 0, 0)
        command = server_command(source, model, sock, fd)
        server = subprocess.Popen(
            command,
            pass_fds=(fd,),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=_limit_stage,
        )
        os.close(fd)
        try:
            healthy = False
            for _ in range(55):
                if server.poll() is not None:
                    break
                if Path(sock).exists():
                    try:
                        status, _ = unix_request(sock, "/health")
                        if status == 200:
                            healthy = True
                            break
                    except (OSError, TimeoutError, http.client.HTTPException):
                        pass
                time.sleep(0.1)
            if not healthy:
                raise ValueError("native_private_stage_not_ready")
            status, catalog = unix_request(sock, "/v1/models", key=token)
            entries = catalog.get("data")
            if status != 200 or not isinstance(entries, list) or len(entries) != 1:
                raise ValueError("native_catalog_unverified")
            body = fixed_prompt(entries[0].get("id"))
            unauth, _ = unix_request(sock, "/v1/chat/completions", payload=body)
            if unauth not in (401, 403):
                raise ValueError("native_api_key_not_enforced")
            authorized, reply = unix_request(sock, "/v1/chat/completions", payload=body, key=token)
            n = validate_generation(authorized, reply)
            print("native_host_unauthed_http=" + str(unauth), flush=True)
            print("native_host_authorized_http=200; output_tokens=" + str(n) + " final_nonblank=True", flush=True)
            # The real Docker container has zero TCP/UDP networking; the only
            # shared host object is an owner-private read-only Unix socket mount.
            result = subprocess.run(
                docker_command(private_dir, os.geteuid(), os.getegid()),
                input=token,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0:
                raise ValueError("native_docker_socket_client_failed")
            lines = result.stdout.splitlines()
            if (
                len(lines) != 3
                or not lines[0].startswith("unix_native_missing_key_http=")
                or not lines[1].startswith("unix_native_authorized_http=200 tokens=")
                or lines[2] != "network_none_unix_socket_auth=PASS; task_admission=HOLD"
            ):
                raise ValueError("native_docker_socket_contract_unverified")
            for line in lines:
                print(line, flush=True)
            return {
                "physical_stage": "PASS_NATIVE_UNIX_API_KEY_INFERENCE",
                "private_socket_only": True,
                "memory_cap_requested_gib": 5,
                "reasoning_budget": 0,
                "docker_network": "none",
                "credentials_persisted": False,
                "approved_task_slots": 0,
                "task_admission": "HOLD",
                "status": "HOLD",
            }
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=3)
            print("ephemeral_native_stage_stopped=" + str(server.poll() is not None), flush=True)
            if any(not Path(f"/proc/{pid}").exists() for pid in original_pids.values()):
                raise ValueError("original_running_model_changed_during_stage")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="explicitly stage one bounded CPU-only native Unix-socket canary"
    )
    args = parser.parse_args(argv)
    if not args.live:
        print(
            json.dumps(
                {"status": "HOLD", "task_admission": "HOLD", "reason": "LIVE_NONADMITTING_STAGE_NOT_REQUESTED"},
                sort_keys=True,
            )
        )
        return 2
    try:
        result = run_live()
    except (
        OSError,
        ValueError,
        TimeoutError,
        subprocess.SubprocessError,
        http.client.HTTPException,
        IndexError,
        TypeError,
        KeyError,
    ):
        result = {
            "status": "HOLD",
            "task_admission": "HOLD",
            "reason": "NATIVE_SOCKET_STAGE_EVIDENCE_INCOMPLETE",
            "approved_task_slots": 0,
        }
    print(json.dumps(result, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
