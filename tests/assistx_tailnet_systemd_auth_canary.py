#!/usr/bin/env python3
"""Opt-in disposable user-systemd sanitizer and authenticated fallback canary.

Only loopback high ports, GET requests, two transient user services, and
an existing live localhost API. This is not a production cutover.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from assistx_tailnet_real_basic_canary import exact_live_state, get_basic_in_memory, status
from assistx_tailnet_sameport_rollback_canary import staged_caddyfile
from assistx_tailnet_sanitizer_canary import free_port


class Denied(RuntimeError):
    pass

def require(ok, reason):
    if not ok:
        raise Denied(reason)

def command(args, timeout=12):
    result=subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    require(result.returncode==0, "transient service action failed")
    return result

def isolated_config(port):
    require(type(port) is int and 1025 < port < 65536 and port not in (8000,19001),
            "invalid disposable listener")
    config=staged_caddyfile(19001,port)
    old="reverse_proxy http://127.0.0.1:19001 {"
    new="reverse_proxy http://127.0.0.1:8000 {"
    require(config.count(old)==1, "pinned configuration changed")
    config=config.replace(old,new,1)
    require(config.count(new)==1
            and "{$ASSISTX_TAILNET_IP}" not in config
            and f"http://127.0.0.1:{port} {{" in config,
            "unsafe or ambiguous disposable binding")
    return config

def verify(port, basic):
    deadline=time.monotonic()+9
    while time.monotonic()<deadline:
        if status(port,{})==401:
            break
        time.sleep(0.15)
    else:
        raise Denied("sanitized read-only route unavailable")
    require(status(port,{"Authorization":basic})==200,
            "actual Basic authentication rejected")
    require(status(port,{"Tailscale-User-Login":"synthetic-forged-identity"})==401,
            "forged identity was accepted")
    require(status(port,{"Authorization":basic,
                         "Tailscale-User-Login":"synthetic-forged-identity"})==200,
            "forged identity interfered with Basic authentication")

def released(port):
    deadline=time.monotonic()+7
    while time.monotonic()<deadline:
        if status(port,{}) is None:
            return True
        time.sleep(0.15)
    raise Denied("temporary unit did not release disposable port")

def unit_name():
    return "assistx-sanitizer-proof-"+uuid.uuid4().hex[:12]+".service"

def stop_unit(name):
    require(re.fullmatch(r"assistx-sanitizer-proof-[a-f0-9]{12}\.service",name) is not None,
            "refusing to stop non-disposable service")
    command(["systemctl","--user","stop",name])

def physical():
    require(os.uname().nodename=="x1-370" and os.geteuid()==1000,
            "nonroot x1-370 only")
    api,hermes,proxy=exact_live_state()
    basic=get_basic_in_memory(api)
    port=free_port()
    require(port not in (8000,19001), "disposable port collision")
    checks={}
    units=[]
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-systemd-auth-proof-") as work:
            directory=Path(work)
            directory.chmod(0o700)
            config=directory/"Caddyfile"
            config.write_text(isolated_config(port))
            command(["/usr/bin/caddy","validate","--config",str(config),
                     "--adapter","caddyfile"])
            checks["pinned_release_derivative_valid"]=True
            for stage in ("primary","fallback"):
                name=unit_name()
                units.append(name)
                command(["systemd-run","--user","--collect","--unit="+name,
                         "--property=Restart=no","--property=NoNewPrivileges=yes",
                         "--property=RuntimeMaxSec=45",
                         "/usr/bin/caddy","run","--config",str(config),
                         "--adapter","caddyfile"])
                verify(port,basic)
                checks[stage+"_basic_and_forgery_denial"]=True
                stop_unit(name)
                checks[stage+"_port_released"]=released(port)
    finally:
        for name in units:
            if re.fullmatch(r"assistx-sanitizer-proof-[a-f0-9]{12}\.service",name):
                subprocess.run(["systemctl","--user","stop",name],
                               capture_output=True,timeout=10,check=False)
    later_api,later_hermes,later_proxy=exact_live_state()
    checks["production_services_unchanged"]=(
        later_api["Image"]==api["Image"] and later_hermes==hermes
        and later_proxy==proxy)
    require(all(checks.values()),"isolated systemd proof incomplete")
    return {"decision":"HOLD","checks":checks,"passed":sum(checks.values()),
            "credentials_logged":False,"response_bodies_read":False,
            "production_task_writes":0,"production_cutover_authorized":False}

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-user-systemd",action="store_true")
    args=parser.parse_args()
    print(json.dumps(physical() if args.physical_user_systemd else {
        "decision":"HOLD","physical_executed":False,
        "production_cutover_authorized":False},sort_keys=True))
