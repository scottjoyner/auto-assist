#!/usr/bin/env python3
"""Opt-in, read-only actual Basic-auth compatibility across loopback Caddy.

Requires x1-370 non-root and exact, already-running AssistX image. Reads the
existing Basic credential from local Docker metadata INTO PROCESS MEMORY ONLY,
uses it for three GET /command-center probes through a disposable Caddy on a
random loopback port, and never logs credentials, headers, or response bodies.
Does not connect to the raw Tailnet listener or mutate production state.

A pass does not authorize a same-port production cutover, nor credential reuse.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from assistx_tailnet_sanitizer_canary import caddyfile, free_port

API_SHA="sha256:74df40c5c720dbef283f83eaf69eae585212748ca3889be508eaa9c88cd5b4c4"
HERMES_SHA="sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307"
ORIGINAL_PROXY="control-room-ts-proxy.service"

class Denied(ValueError):
    pass

def require(ok,why):
    if not ok:
        raise Denied(why)

def run(args,timeout=8):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    require(result.returncode==0,"a required read-only control failed")
    return result.stdout

def exact_live_state():
    api=json.loads(run(["docker","inspect","--format","{{json .}}","assistx-api"]))
    require(api.get("Image")==API_SHA and api.get("State",{}).get("Running") is True,
            "API image identity or runtime changed")
    hermes=json.loads(run(["docker","inspect","--format","{{json .}}",
                            "assistx-hermes-adapter"]))
    require(hermes.get("Image")==HERMES_SHA and
            hermes.get("State",{}).get("Running") is True and
            len(hermes.get("Mounts",[]))==16,
            "Hermes runtime identity/mounts changed")
    proxy=run(["systemctl","--user","show",ORIGINAL_PROXY,
               "--property=ActiveState,MainPID"])
    require("ActiveState=active" in proxy,"original Tailnet proxy stopped")
    return api,hermes.get("Image"),proxy

def get_basic_in_memory(api):
    values={}
    for entry in api.get("Config",{}).get("Env",[]):
        key,sep,value=entry.partition("=")
        require(bool(sep) and key not in values,"ambiguous service environment")
        values[key]=value
    require(values.get("TRUSTED_AUTH_HEADER")=="Tailscale-User-Login",
            "unexpected legacy identity header name")
    user=values.get("BASIC_AUTH_USER")
    password=values.get("BASIC_AUTH_PASS")
    require(type(user) is str and bool(user) and type(password) is str
            and bool(password),"Basic auth not provisioned")
    return "Basic "+base64.b64encode((user+":"+password).encode()).decode()

def status(port,headers):
    url=f"http://127.0.0.1:{port}/command-center"
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req=urllib.request.Request(url,method="GET",headers=headers)
    try:
        # Deliberately do NOT read any response body or collect its contents.
        with opener.open(req,timeout=4) as resp:
            return resp.status
    except urllib.error.HTTPError as error:
        status_code=error.code
        error.close()
        return status_code
    except (urllib.error.URLError,TimeoutError,OSError):
        return None

def physical():
    require(os.uname().nodename=="x1-370" and os.geteuid()==1000,
            "read-only nonroot x1-370 test only")
    require(Path("/usr/bin/caddy").is_file(),"real Caddy unavailable")
    api,hermes,proxy=exact_live_state()
    authorization=get_basic_in_memory(api)
    port=free_port()
    process=None
    checks={}
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-basic-auth-canary-") as temp:
            p=Path(temp)
            p.chmod(0o700)
            config=p/"Caddyfile"
            config.write_text(caddyfile(8000,port),encoding="utf-8")
            result=subprocess.run(["/usr/bin/caddy","validate","--config",
                    str(config),"--adapter","caddyfile"],
                    capture_output=True,text=True,timeout=10)
            require(result.returncode==0,"disposable sanitizer syntax invalid")
            checks["isolated_real_caddy_config_valid"]=True
            process=subprocess.Popen(["/usr/bin/caddy","run","--config",
                    str(config),"--adapter","caddyfile"],
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,start_new_session=True)
            ready=False
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                require(process.poll() is None,"disposable Caddy exited")
                if status(port,{})==401:
                    ready=True
                    break
                time.sleep(0.15)
            require(ready,"sanitized API read-only route unavailable")
            checks["unsigned_request_denied"]=True
            # Real configured Basic auth is used on the same machine only.
            # Synthetic header is intentionally included to prove it is
            # STRIPPED before reaching the production legacy auth helper.
            auth_only=status(port,{"Authorization":authorization})
            mixed=status(port,{"Authorization":authorization,
                        "Tailscale-User-Login":"synthetic-forged-identity"})
            spoof_only=status(port,{"Tailscale-User-Login":"synthetic-forged-identity"})
            require(auth_only==200,"actual Basic-only read-only GET was rejected")
            require(mixed==200,"synthetic header interfered with valid Basic auth")
            require(spoof_only==401,"synthetic identity authorized through sanitizer")
            checks["configured_basic_readonly_get_accepted"]=True
            checks["configured_basic_plus_forgery_still_accepted_as_basic"]=True
            checks["forged_header_without_basic_denied"]=True
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    checks["disposable_caddy_exited_and_config_deleted"]=True
    after_api,after_hermes,after_proxy=exact_live_state()
    checks["production_images_and_proxy_unchanged"]=(
        after_api["Image"]==api["Image"] and after_hermes==hermes
        and after_proxy==proxy)
    require(all(checks.values()),"read-only authentication witness incomplete")
    return {
        "decision":"HOLD",
        "checks":checks,
        "passed":sum(checks.values()),
        "real_production_credentials_logged":False,
        "production_response_bodies_read":False,
        "production_task_mutations":0,
        "raw_tailnet_forwarder_touched":False,
        "production_cutover_authorized":False,
    }

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-readonly-basic",action="store_true")
    args=parser.parse_args()
    report=physical() if args.physical_readonly_basic else {
        "decision":"HOLD","physical_executed":False,
        "production_cutover_authorized":False,
        "production_task_mutations":0}
    print(json.dumps(report,sort_keys=True))
