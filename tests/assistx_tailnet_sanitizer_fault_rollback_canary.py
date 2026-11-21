#!/usr/bin/env python3
"""Fail-closed user-systemd sanitizer rollback fault injection.

Never binds Tailnet or production :8000. Exercises failure of a deliberately
invalid Caddy unit between two healthy pinned, header-stripping Caddy units
on the same random loopback-only disposable port.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from assistx_tailnet_real_basic_canary import exact_live_state, get_basic_in_memory, status
from assistx_tailnet_sanitizer_canary import free_port
from assistx_tailnet_systemd_auth_canary import (
    command, isolated_config, released, require, stop_unit, unit_name, verify,
)

class Denied(RuntimeError):
    pass

def safe_name(name):
    return re.fullmatch(r"assistx-sanitizer-proof-[a-f0-9]{12}\.service",name) is not None

def start_disposable(name, config):
    require(safe_name(name),"invalid research-only unit identifier")
    return command(["systemd-run","--user","--collect","--unit="+name,
                    "--property=Restart=no",
                    "--property=NoNewPrivileges=yes",
                    "--property=RuntimeMaxSec=45",
                    "/usr/bin/caddy","run","--config",str(config),
                    "--adapter","caddyfile"],timeout=12)

def service_state(name):
    require(safe_name(name),"invalid research-only unit identifier")
    read=subprocess.run(
        ["systemctl","--user","show",name,
         "--property=ActiveState,SubState,MainPID"],
        capture_output=True,text=True,timeout=8,check=False)
    return read.stdout if read.returncode==0 else ""

def await_failed(name,port):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        state=service_state(name)
        if (("ActiveState=failed" in state or "ActiveState=inactive" in state
             or not state)
                and status(port,{}) is None):
            return True
        time.sleep(0.1)
    raise Denied("deliberately malformed service did not fail and release port")

def physical():
    require(os.uname().nodename=="x1-370" and os.geteuid()==1000,
            "nonroot x1-370 research operator only")
    old_api,old_hermes,old_proxy=exact_live_state()
    basic=get_basic_in_memory(old_api)
    port=free_port()
    require(type(port) is int and 1025<port<65536 and port not in (8000,19001),
            "refusing production or fixture port")
    units=[]
    checks={}
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-sanitizer-fault-") as tmp:
            root=Path(tmp)
            root.chmod(0o700)
            good=root/"pinned.Caddyfile"
            bad=root/"deliberately-invalid.Caddyfile"
            good.write_text(isolated_config(port),encoding="utf-8")
            bad.write_text(good.read_text().replace(
                "    reverse_proxy http://127.0.0.1:8000 {",
                "    deliberately_invalid_caddy_directive {" ,1),encoding="utf-8")
            require(good.read_text()!=bad.read_text(),"fault not injected")
            # Validate good release derivative, and require invalid config to
            # fail Caddy's real parser before trying a transient process.
            validate_good=subprocess.run(
                ["/usr/bin/caddy","validate","--config",str(good),
                 "--adapter","caddyfile"],
                text=True,capture_output=True,timeout=10,check=False)
            validate_bad=subprocess.run(
                ["/usr/bin/caddy","validate","--config",str(bad),
                 "--adapter","caddyfile"],
                text=True,capture_output=True,timeout=10,check=False)
            require(validate_good.returncode==0 and validate_bad.returncode!=0,
                    "expected good/invalid config distinction absent")
            checks["pinned_good_and_invalid_upgrade_distinguished"]=True
            # Phase 1: matching current behavior but safely sanitizing.
            primary=unit_name()
            units.append(primary)
            start_disposable(primary,good)
            verify(port,basic)
            checks["initial_sanitized_basic_and_forgery_checks"]=True
            stop_unit(primary)
            released(port)
            checks["primary_port_released"]=True
            # Phase 2: specifically exercise user-systemd startup failure.
            faulty=unit_name()
            units.append(faulty)
            attempt=subprocess.run(
                ["systemd-run","--user","--collect","--unit="+faulty,
                 "--property=Restart=no",
                 "--property=NoNewPrivileges=yes",
                 "--property=RuntimeMaxSec=12",
                 "/usr/bin/caddy","run","--config",str(bad),
                 "--adapter","caddyfile"],
                capture_output=True,text=True,timeout=10,check=False)
            require(attempt.returncode==0,
                    "faulty transient unit did not reach systemd start")
            checks["invalid_transient_rejected_no_listener"]=await_failed(faulty,port)
            # Phase 3: previously tested pinned sanitizer, never raw socat.
            backup=unit_name()
            units.append(backup)
            start_disposable(backup,good)
            verify(port,basic)
            checks["sanitized_fallback_same_port_authenticated"]=True
            stop_unit(backup)
            released(port)
            checks["sanitized_fallback_port_released"]=True
    finally:
        for name in units:
            if safe_name(name):
                subprocess.run(["systemctl","--user","stop",name],
                               capture_output=True,timeout=10,check=False)
    later_api,later_hermes,later_proxy=exact_live_state()
    checks["original_api_hermes_and_proxy_unchanged"]=(
        later_api["Image"]==old_api["Image"]
        and later_hermes==old_hermes and later_proxy==old_proxy)
    require(all(checks.values()),"fault-injection result incomplete")
    return {
        "decision":"HOLD","passed":sum(checks.values()),"checks":checks,
        "production_port_used":False,"production_task_writes":0,
        "synthetic_identity_header_stripped":True,
        "real_basic_credential_values_logged":False,
        "rollback_to_unsafe_socat_performed":False,
        "real_production_cutover_verified":False,
    }

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-disposable-user-systemd",action="store_true")
    args=parser.parse_args()
    print(json.dumps(physical() if args.physical_disposable_user_systemd else {
        "decision":"HOLD","physical_executed":False,
        "real_production_cutover_verified":False},sort_keys=True))
