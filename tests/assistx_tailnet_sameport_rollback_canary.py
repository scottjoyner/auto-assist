#!/usr/bin/env python3
"""Disposable same-port socat -> sanitizing Caddy -> safe Caddy rollback.

No production services, Tailnet address, Docker metadata, user passwords,
real API requests, or tasks. Uses random LOCALHOST-only ports and a synthetic
echo backend. Demonstrates availability restoration without reintroducing a
trusted-header bypass in the fallback stage. Always a release HOLD.
"""
from __future__ import annotations

import argparse
import base64
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

from assistx_tailnet_sanitizer_canary import caddyfile, free_port

CADDY="/usr/bin/caddy"
SOCAT="/usr/bin/socat"

class UnsafeRehearsal(ValueError):
    pass

def need(truth,reason):
    if not truth:
        raise UnsafeRehearsal(reason)

class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        content=json.dumps({
            "forwarded_identity":bool(self.headers.get("Tailscale-User-Login")),
            "basic_auth_forwarded":self.headers.get("Authorization","").startswith("Basic "),
            "path":self.path,
        },sort_keys=True).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(content)))
        self.end_headers()
        self.wfile.write(content)
    def log_message(self,*args):
        return

def request(port):
    basic=base64.b64encode(b"synthetic-user:synthetic-pass").decode()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req=urllib.request.Request(
        f"http://127.0.0.1:{port}/synthetic-probe",
        method="GET",
        headers={
            "Authorization":"Basic "+basic,
            "Tailscale-User-Login":"untrusted-synthetic-identity",
        })
    with opener.open(req,timeout=2) as r:
        need(r.status==200,"synthetic backend rejected request")
        return json.loads(r.read(2048))

def wait_ready(proc,port,*,expect_identity,timeout=7):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        need(proc.poll() is None,"disposable listener terminated")
        try:
            answer=request(port)
            need(answer["path"]=="/synthetic-probe"
                 and answer["basic_auth_forwarded"]
                 and answer["forwarded_identity"] is expect_identity,
                 "proxy identity or Basic forwarding did not match state")
            return
        except (OSError,ValueError,urllib.error.URLError):
            time.sleep(0.1)
    raise UnsafeRehearsal("disposable listener did not start")

def stop(proc):
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)

def physical():
    need(os.uname().nodename=="x1-370" and os.geteuid()==1000,
         "non-root isolated x1-370 only")
    need(Path(CADDY).is_file() and Path(SOCAT).is_file(),
         "expected Caddy and socat binaries unavailable")
    backend=ThreadingHTTPServer(("127.0.0.1",0),Echo)
    backend_port=backend.server_address[1]
    port=free_port()
    while port==backend_port:
        port=free_port()
    task=threading.Thread(target=backend.serve_forever,daemon=True)
    task.start()
    current=None
    checks={}
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-sanitizer-rollback-") as work:
            folder=Path(work)
            folder.chmod(0o700)
            config=folder/"Caddyfile"
            config.write_text(caddyfile(backend_port,port))
            validated=subprocess.run(
                [CADDY,"validate","--config",str(config),"--adapter","caddyfile"],
                capture_output=True,text=True,timeout=10)
            need(validated.returncode==0,"sanitizer fallback config not valid")
            checks["safe_fallback_configuration_valid"]=True
            # Stage 1: reproduce the *synthetic* legacy TCP forwarder.
            current=subprocess.Popen([
                SOCAT,f"TCP-LISTEN:{port},bind=127.0.0.1,fork,reuseaddr",
                f"TCP:127.0.0.1:{backend_port}"],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,start_new_session=True)
            wait_ready(current,port,expect_identity=True)
            checks["synthetic_raw_socat_forwards_unsafe_header"]=True
            stop(current)
            current=None
            # Stage 2: same port, header-stripping replacement.
            current=subprocess.Popen([
                CADDY,"run","--config",str(config),"--adapter","caddyfile"],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,start_new_session=True)
            wait_ready(current,port,expect_identity=False)
            checks["same_port_caddy_preserves_basic_but_strips_identity"]=True
            stop(current)
            current=None
            # Stage 3: rollback to pinned *sanitizing Caddy*, not raw socat.
            # A fallback to socat would restore the original trust weakness.
            current=subprocess.Popen([
                CADDY,"run","--config",str(config),"--adapter","caddyfile"],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,start_new_session=True)
            wait_ready(current,port,expect_identity=False)
            checks["sanitizing_rollback_remains_fail_closed"]=True
    finally:
        stop(current)
        backend.shutdown()
        backend.server_close()
        task.join(timeout=3)
    checks["all_disposable_listener_processes_cleaned"]=True
    need(all(checks.values()),"incomplete synthetic rollback witness")
    return {
        "decision":"HOLD",
        "checks":checks,
        "passed":sum(checks.values()),
        "real_tailnet_or_production_changes":0,
        "real_credentials_or_tasks_accessed":0,
        "old_socat_rollback_security_safe":False,
        "real_client_compatibility_proven":False,
        "operator_cutover_authorized":False,
    }

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-loopback",action="store_true")
    args=parser.parse_args()
    print(json.dumps(physical() if args.physical_loopback else {
        "decision":"HOLD",
        "physical_executed":False,
        "real_tailnet_or_production_changes":0,
        "operator_cutover_authorized":False,
    },sort_keys=True))
