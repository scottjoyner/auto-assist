#!/usr/bin/env python3
"""Inert by default. Qualify real Caddy identity-header stripping in loopback.

Active mode starts a disposable local HTTP backend and a separate local Caddy
process; then checks the actual live AssistX only through the header-stripping
candidate, using public/unauthenticated GET endpoints and a SYNTHETIC forged
identity header. It never starts, stops, or changes the production proxy.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

BACKEND_HOST="127.0.0.1"
CADDY_BIN="/usr/bin/caddy"
PROD_API="http://127.0.0.1:8000"
EXPECTED_AUTH_HEADER="Tailscale-User-Login"
DROPPED_HEADERS=(
    EXPECTED_AUTH_HEADER,
    "X-Forwarded-User",
    "X-Authenticated-User",
    "X-Remote-User",
    "Remote-User",
    "X-Forwarded-Email",
)

class UnsafeCanary(ValueError):
    pass

def need(ok: bool, reason: str) -> None:
    if not ok:
        raise UnsafeCanary(reason)

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind((BACKEND_HOST,0))
        return sock.getsockname()[1]

def caddyfile(upstream_port: int,listen_port: int) -> str:
    need(1025 < upstream_port < 65536 and 1025 < listen_port < 65536
         and upstream_port!=listen_port,"invalid isolated loopback ports")
    strips="\n".join(f"        header_up -{name}" for name in DROPPED_HEADERS)
    return (
        "{\n    admin off\n    auto_https off\n}\n"
        f"http://127.0.0.1:{listen_port} {{\n"
        f"    reverse_proxy http://127.0.0.1:{upstream_port} {{\n"
        +strips+"\n    }\n}\n")

class SyntheticHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path=="/synthetic-events":
            return self.events()
        if self.path=="/synthetic-websocket":
            return self.websocket()
        self.answer()
    def websocket(self):
        key=self.headers.get("Sec-WebSocket-Key","")
        if (self.headers.get("Upgrade","").lower()!="websocket"
                or not key):
            self.send_error(400)
            return
        digest=hashlib.sha1((key+
            "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        accept=base64.b64encode(digest).decode()
        sanitized=all(self.headers.get(n) is None for n in DROPPED_HEADERS)
        self.send_response(101,"Switching Protocols")
        self.send_header("Upgrade","websocket")
        self.send_header("Connection","Upgrade")
        self.send_header("Sec-WebSocket-Accept",accept)
        self.end_headers()
        payload=b"OK" if sanitized else b"NO"
        self.wfile.write(bytes((0x81,len(payload)))+payload)
        self.wfile.flush()
        self.close_connection=True
    def events(self):
        dropped=all(self.headers.get(key) is None for key in DROPPED_HEADERS)
        raw=("data: "+json.dumps({"identity_headers_stripped":dropped})+"\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type","text/event-stream")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_POST(self):
        self.answer()
    def answer(self):
        headers={k.lower():self.headers.get(k) for k in DROPPED_HEADERS}
        remaining=(None if not self.headers.get("Authorization") else
                   self.headers.get("Authorization").startswith("Basic "))
        record={
            "dropped_headers_absent":all(value is None for value in headers.values()),
            "basic_auth_forwarded":remaining is True,
            "method":self.command,
            "path":self.path,
            "upgrade":self.headers.get("Upgrade")=="websocket",
        }
        raw=json.dumps(record,sort_keys=True).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def log_message(self,*args):
        pass

def urlopen_no_proxy(url,*,headers=None,method="GET",data=None,timeout=4):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(urllib.request.Request(
        url,method=method,data=data,headers=headers or {}),timeout=timeout)

def check_websocket_upgrade(port:int) -> bool:
    key=base64.b64encode(b"isolated-research").decode()
    headers=[
        "GET /synthetic-websocket HTTP/1.1",
        f"Host: {BACKEND_HOST}:{port}",
        "Connection: Upgrade", "Upgrade: websocket",
        f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
        "Tailscale-User-Login: synthetic-untrusted-identity",
        "X-Forwarded-User: synthetic-untrusted-identity",
        "", "",
    ]
    with socket.create_connection((BACKEND_HOST,port),timeout=3) as sock:
        sock.settimeout(3)
        sock.sendall("\r\n".join(headers).encode("ascii"))
        data=b""
        while b"\r\n\r\n" not in data and len(data)<4096:
            chunk=sock.recv(4096)
            if not chunk:
                break
            data+=chunk
        need(data.startswith(b"HTTP/1.1 101 ") or data.startswith(b"HTTP/1.0 101 "),
             "reverse proxy did not preserve WebSocket 101 upgrade")
        header,_,body=data.partition(b"\r\n\r\n")
        while len(body)<4:
            chunk=sock.recv(128)
            if not chunk:
                break
            body+=chunk
        need(body[:4]==bytes((0x81,2))+b"OK",
             "WebSocket synthetic backend saw untrusted identity header")
    return True


def check_http_proxy(port:int, upstream_label:str)->dict:
    forged={k:"synthetic-untrusted-identity" for k in DROPPED_HEADERS}
    forged["Authorization"]="Basic "+base64.b64encode(
        b"synthetic-user:synthetic-password").decode()
    with urlopen_no_proxy(f"http://{BACKEND_HOST}:{port}/synthetic",
                          headers=forged) as resp:
        read=json.loads(resp.read(4096))
        need(resp.status==200 and read["dropped_headers_absent"]
             and read["basic_auth_forwarded"],
             "Caddy failed to strip identity or preserve Basic auth")
    with urlopen_no_proxy(f"http://{BACKEND_HOST}:{port}/synthetic",
                          headers=forged,method="POST",data=b"synthetic-only") as resp:
        post=json.loads(resp.read(4096))
        need(resp.status==200 and post["dropped_headers_absent"]
             and post["basic_auth_forwarded"] and post["method"]=="POST",
             "Caddy POST identity sanitation regression")
    with urlopen_no_proxy(
        f"http://{BACKEND_HOST}:{port}/synthetic-events",headers=forged
    ) as response:
        data=response.read(2048)
        need(response.status==200
             and response.headers.get("Content-Type")=="text/event-stream"
             and data.startswith(b"data: ")
             and json.loads(data[6:].strip())["identity_headers_stripped"] is True,
             "Caddy stripped SSE response or forwarded untrusted identity")
    need(check_websocket_upgrade(port),
         "Caddy failed to preserve WebSocket and strip identity")
    return {"synthetic_get_header_stripped":True,
            "synthetic_post_header_stripped":True,
            "synthetic_basic_auth_preserved":True,
            "synthetic_sse_stream_preserved_and_sanitized":True,
            "synthetic_websocket_upgrade_sanitized":True}

def physical():
    need(os.uname().nodename=="x1-370" and os.geteuid()==1000,
         "non-root x1-370 research operator only")
    need(Path(CADDY_BIN).is_file(),"caddy binary not locally installed")
    need(os.path.isdir("/home/scott/git/wt-assistx-hermes-split-root-20261021"),
         "unexpected research workspace")
    live=subprocess.run(["docker","inspect","--format",
        "{{.Image}} {{.State.Running}}","assistx-api"],
        capture_output=True,text=True,timeout=8,check=True).stdout.strip()
    need(live=="sha256:74df40c5c720dbef283f83eaf69eae585212748ca3889be508eaa9c88cd5b4c4 true",
         "original API changed")
    fake_server=ThreadingHTTPServer((BACKEND_HOST,0),SyntheticHandler)
    backend_port=fake_server.server_address[1]
    gateway_port=free_port()
    while gateway_port==backend_port:
        gateway_port=free_port()
    thread=threading.Thread(target=fake_server.serve_forever,daemon=True)
    thread.start()
    checks={}
    proxy=None
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-tailnet-header-caddy-") as work:
            home=Path(work)
            home.chmod(0o700)
            config=home/"Caddyfile"
            config.write_text(caddyfile(backend_port,gateway_port),encoding="utf-8")
            parsed=subprocess.run(
                [CADDY_BIN,"validate","--config",str(config),"--adapter","caddyfile"],
                capture_output=True,text=True,timeout=12)
            need(parsed.returncode==0,"isolated sanitizer config not valid")
            checks["real_caddy_config_validated"]=True
            proxy=subprocess.Popen(
                [CADDY_BIN,"run","--config",str(config),"--adapter","caddyfile"],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,start_new_session=True)
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                if proxy.poll() is not None:
                    raise UnsafeCanary("disposable Caddy exited")
                try:
                    checks.update(check_http_proxy(gateway_port,"synthetic"))
                    break
                except (OSError,KeyError,ValueError,urllib.error.URLError):
                    time.sleep(0.15)
            need(checks.get("synthetic_post_header_stripped") is True,
                 "Caddy sanitizer synthetic end-to-end failed")
            checks["loopback_only_isolated_candidate"]=True
            # A separate, second disposable candidate forwards to the
            # REAL local API, never a production service or remote listener.
            # To avoid touching credentials, only unauthenticated GET /health
            # and authenticated-only GET /command-center are tested.
            config.write_text(caddyfile(8000,gateway_port),encoding="utf-8")
            # Caddy config is loaded only by the new disposable subprocess.
            proxy.terminate()
            proxy.wait(timeout=5)
            proxy=None
            time.sleep(0.2)
            proxy=subprocess.Popen(
                [CADDY_BIN,"run","--config",str(config),"--adapter","caddyfile"],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,start_new_session=True)
            ready=False
            for _ in range(30):
                if proxy.poll() is not None:
                    raise UnsafeCanary("live-backend-only Caddy candidate exited")
                try:
                    with urlopen_no_proxy(f"http://{BACKEND_HOST}:{gateway_port}/health",
                                          timeout=2) as response:
                        ready=response.status==200
                    if ready:
                        break
                except (OSError,urllib.error.URLError):
                    time.sleep(0.15)
            need(ready,"live API health not reachable through isolated sanitizer")
            checks["live_health_get_preserved"]=True
            # Fake identity must not authorize command center through the
            # sanitizer. Never query the raw API with a forged header.
            synthetic_identity={
                EXPECTED_AUTH_HEADER:"synthetic-untrusted-identity",
                "X-Forwarded-User":"synthetic-untrusted-identity",
            }
            try:
                with urlopen_no_proxy(
                    f"http://{BACKEND_HOST}:{gateway_port}/command-center",
                    headers=synthetic_identity,timeout=3) as response:
                    status=response.status
            except urllib.error.HTTPError as error:
                status=error.code
            need(status==401,"candidate failed unauthenticated live route denial")
            checks["live_protected_get_rejects_synthetic_identity"]=True
    finally:
        if proxy is not None:
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=4)
        fake_server.shutdown()
        fake_server.server_close()
        thread.join(timeout=2)
    after=subprocess.run(["docker","inspect","--format",
        "{{.Image}} {{.State.Running}}","assistx-api"],
        capture_output=True,text=True,timeout=8,check=True).stdout.strip()
    need(after==live,"production API identity changed")
    checks["production_api_unchanged"]=True
    checks["isolated_caddy_process_cleaned"]=True
    return {"decision":"HOLD","checks":checks,
            "count_passed":sum(checks.values()),
            "original_service_modified":False,
            "actual_client_credentials_used":False,
            "real_task_mutations":0}

if __name__=="__main__":
    args=argparse.ArgumentParser(description=__doc__)
    args.add_argument("--physical-loopback",action="store_true")
    parsed=args.parse_args()
    print(json.dumps(physical() if parsed.physical_loopback else
         {"decision":"HOLD","physical_executed":False,
          "original_service_modified":False},sort_keys=True))
