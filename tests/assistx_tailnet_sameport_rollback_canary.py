#!/usr/bin/env python3
"""Disposable same-port socat -> sanitizing Caddy -> safe Caddy rollback.

No production services, Tailnet address, Docker metadata, user passwords,
real API requests, or tasks. Uses random LOCALHOST-only ports and a synthetic
echo backend. Demonstrates availability restoration without reintroducing a
trusted-header bypass in the fallback stage. Always a release HOLD.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from assistx_tailnet_sanitizer_canary import DROPPED_HEADERS, free_port

CADDY="/usr/bin/caddy"
SOCAT="/usr/bin/socat"
RELEASE_CADDYFILE=(Path(__file__).resolve().parents[1]/
                   "scripts/assistx-tailnet-header-sanitizer.Caddyfile")
# These bytes are the proposed production config, never the test helper.
REVIEWED_CADDYFILE_SHA256="2efa50602a821a9dffc6704496eb1cd0c0988e81c2f1fce65c3b9ad249e2a444"

class UnsafeRehearsal(ValueError):
    pass

def need(truth,reason):
    if not truth:
        raise UnsafeRehearsal(reason)

def staged_caddyfile(upstream_port:int,listen_port:int)->str:
    """Rebind only the *pinned release config* to disposable loopback ports.

    Never generate an independent simulator of the identity-stripping rules.
    Every unexpected source change fails closed until separately reviewed.
    """
    need(type(upstream_port) is int and type(listen_port) is int
         and 1025 < upstream_port < 65536
         and 1025 < listen_port < 65536
         and upstream_port != listen_port
         and upstream_port != 8000 and listen_port != 8000,
         "not two isolated disposable loopback ports")
    need(RELEASE_CADDYFILE.is_file() and not RELEASE_CADDYFILE.is_symlink(),
         "pinned production Caddyfile absent or symlinked")
    raw=RELEASE_CADDYFILE.read_bytes()
    need(hashlib.sha256(raw).hexdigest()==REVIEWED_CADDYFILE_SHA256,
         "staged production Caddyfile changed without renewed review")
    config=raw.decode("utf-8")
    listener="http://{$ASSISTX_TAILNET_IP}:8000 {"
    backend="reverse_proxy http://127.0.0.1:8000 {"
    need(config.count(listener)==1 and config.count(backend)==1
         and config.count("reverse_proxy")==1
         and config.count("admin off")==1
         and config.count("auto_https off")==1,
         "staged production proxy structure ambiguous")
    for name in DROPPED_HEADERS:
        need(config.count("header_up -"+name)==1,
             "staged release missing identity sanitization")
    patched=config.replace(listener,
                f"http://127.0.0.1:{listen_port} {{",1)
    patched=patched.replace(backend,
                f"reverse_proxy http://127.0.0.1:{upstream_port} {{",1)
    need("{$ASSISTX_TAILNET_IP}" not in patched
         and "http://127.0.0.1:8000" not in patched,
         "disposable config retained production network endpoints")
    return patched


class Echo(BaseHTTPRequestHandler):
    def do_POST(self):
        return self.do_GET()
    def do_GET(self):
        content=json.dumps({
            "forwarded_identity":bool(self.headers.get("Tailscale-User-Login")),
            "basic_auth_forwarded":self.headers.get("Authorization","").startswith("Basic "),
            "path":self.path,
            "method":self.command,
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

def exact_opencode_dispatch(port: int) -> bool:
    """Invoke the checked-in OpenCode bridge _post, never a real endpoint.

    Compile only the named function so module-level dispatch/sync setup
    cannot run. The one outgoing URL is bounded to our synthetic 127.0.0.1
    backend via the temporary sanitizer; no real production task is sent.
    """
    source=(Path(__file__).resolve().parents[1]/
            "scripts/opencode-bridge/dispatch_assistx_task.py")
    need(source.is_file() and not source.is_symlink(),
         "OpenCode bridge dispatch source unavailable")
    node=ast.parse(source.read_text())
    fn=next((item for item in node.body
             if isinstance(item,ast.FunctionDef) and item.name=="_post"),None)
    need(fn is not None,"exact OpenCode dispatch function missing")
    env={"json":json,"urllib":urllib,"base64":base64,"sys":sys}
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[fn],type_ignores=[])),str(source),"exec"),env)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with patch.object(urllib.request,"urlopen",
                      side_effect=lambda request,timeout=30:
                          opener.open(request,timeout=3)):
        result=env["_post"](
            f"http://127.0.0.1:{port}/synthetic-opencode-dispatch",
            {"source":"synthetic-opencode-contract"},
            "synthetic-opencode","synthetic-pass")
    need(type(result) is dict
         and result.get("method")=="POST"
         and result.get("basic_auth_forwarded") is True
         and result.get("forwarded_identity") is False,
         "actual OpenCode bridge Basic request not sanitized")
    return True


def exact_opencode_config_sync(port: int) -> bool:
    """Invoke the actual OpenCode config-sync _post twice against echo only.

    No Hermes config scans, model endpoint registration, probes, router
    refreshes, real credentials or production network targets are run.
    Only the named checked-in request-construction function is extracted.
    """
    source=(Path(__file__).resolve().parents[1]/
            "scripts/opencode-bridge/sync_configs_to_assistx.py")
    need(source.is_file() and not source.is_symlink(),
         "OpenCode config-sync source unavailable")
    node=ast.parse(source.read_text())
    fn=next((item for item in node.body
             if isinstance(item,ast.FunctionDef) and item.name=="_post"),None)
    need(fn is not None,"exact config-sync POST function missing")
    env={"json":json,"urllib":urllib,"base64":base64,"sys":sys}
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[fn],type_ignores=[])),str(source),"exec"),env)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with patch.object(urllib.request,"urlopen",
                      side_effect=lambda request,timeout=30:
                          opener.open(request,timeout=3)):
        responses=[]
        for route in (
            "/api/swarm/model-endpoints/register",
            "/api/swarm/model-endpoints/synthetic/probe",
        ):
            responses.append(env["_post"](
                f"http://127.0.0.1:{port}{route}",
                {"source":"synthetic-config-sync-contract"},
                "synthetic-opencode","synthetic-pass"))
    need(len(responses)==2 and all(type(v) is dict
         and v.get("method")=="POST"
         and v.get("basic_auth_forwarded") is True
         and v.get("forwarded_identity") is False for v in responses),
         "OpenCode config-sync POST did not preserve Basic and sanitize identity")
    return True


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
            config.write_text(staged_caddyfile(backend_port,port))
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
            checks["real_opencode_bridge_dispatch_contract_sanitized"]=(
                exact_opencode_dispatch(port))
            checks["real_opencode_config_sync_register_and_probe_sanitized"]=(
                exact_opencode_config_sync(port))
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
