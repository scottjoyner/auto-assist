#!/usr/bin/env python3
"""Read-only release preflight for replacing socat with Caddy header sanitizer.

Never changes systemd, docker, firewall, Tailscale, API credentials or tasks.
Exit is HOLD even with valid config: operator cutover requires real client
compatibility, version-pinned recovery and rollback evidence.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
# Kept source-local for a preflight runnable without external PYTHONPATH.
# Unit tests assert equality with the physical canary's stripped-header set.
DROPPED_HEADERS=(
    "Tailscale-User-Login",
    "X-Forwarded-User",
    "X-Authenticated-User",
    "X-Remote-User",
    "Remote-User",
    "X-Forwarded-Email",
)
CADDYFILE=ROOT/"scripts/assistx-tailnet-header-sanitizer.Caddyfile"
IMAGE="sha256:74df40c5c720dbef283f83eaf69eae585212748ca3889be508eaa9c88cd5b4c4"

class Blocked(ValueError):
    pass

def must(ok,reason):
    if not ok:
        raise Blocked(reason)

def inspect_contract(config:str,*,socat_active:bool,
                     header_configured:bool,header_expected:bool,
                     basic_available:bool) -> dict:
    must(isinstance(config,str) and len(config)<4096
         and config.count("reverse_proxy")==1,
         "unexpected Caddy config shape")
    must("http://{$ASSISTX_TAILNET_IP}:8000 {" in config
         and "reverse_proxy http://127.0.0.1:8000 {" in config,
         "must bind only Tailnet while forwarding to loopback")
    must("admin off" in config and "auto_https off" in config,
         "disable automatic management and external TLS")
    for name in DROPPED_HEADERS:
        must(config.count("header_up -"+name)==1,
             "missing or duplicate identity stripping rule")
    must(socat_active, "expected existing proxy is not active")
    must(header_configured and header_expected,
         "original auth header identity contract unverified")
    must(basic_available, "Basic compatibility path is unconfigured")
    return {
        "decision":"HOLD",
        "sanitizer_config_exact_rules_valid":True,
        "existing_socat_service_active":socat_active,
        "existing_legacy_header_configured":header_configured,
        "existing_header_is_expected_tailscale_login":header_expected,
        "basic_authentication_credentials_present":basic_available,
        "independent_client_compatibility_verified":False,
        "websocket_acceptance_verified":False,
        "release_rollback_verified":False,
        "automatic_cutover_authorized":False,
        "production_mutations":0,
        "credential_values_exposed":False,
    }

def call(args,timeout=8):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout)

def physical():
    must(os.uname().nodename=="x1-370" and os.geteuid()==1000,
         "read-only x1-370 research operator only")
    must(CADDYFILE.is_file() and not CADDYFILE.is_symlink(),
         "reviewed Caddyfile unavailable")
    config=CADDYFILE.read_text()
    ip=call(["tailscale","ip","-4"])
    must(ip.returncode==0 and len(ip.stdout.strip().splitlines())==1,
         "Tailnet IPv4 unavailable")
    addr=ipaddress.ip_address(ip.stdout.strip())
    must(addr.version==4 and addr in ipaddress.ip_network("100.64.0.0/10"),
         "expected Tailnet address missing")
    service=call(["systemctl","--user","show","control-room-ts-proxy.service",
                  "--property=ActiveState,ExecStart"])
    must(service.returncode==0 and "ActiveState=active" in service.stdout
         and f"TCP-LISTEN:8000,bind={addr},fork,reuseaddr" in service.stdout
         and "TCP:127.0.0.1:8000" in service.stdout,
         "raw forwarded service changed")
    snapshot=call(["docker","inspect","--format","{{json .}}","assistx-api"])
    must(snapshot.returncode==0 and len(snapshot.stdout)<750000,
         "production API inspect unavailable")
    production=json.loads(snapshot.stdout)
    must(production.get("Image")==IMAGE
         and production.get("State",{}).get("Running") is True,
         "unexpected production image or state")
    values={}
    for entry in production.get("Config",{}).get("Env",[]):
        k,sep,v=entry.partition("=")
        must(bool(sep) and k not in values,"ambiguous Docker environment")
        values[k]=v
    evidence=inspect_contract(
        config,
        socat_active=True,
        header_configured=bool(values.get("TRUSTED_AUTH_HEADER")),
        header_expected=values.get("TRUSTED_AUTH_HEADER")=="Tailscale-User-Login",
        basic_available=bool(values.get("BASIC_AUTH_USER"))
                        and bool(values.get("BASIC_AUTH_PASS")),
    )
    # Validate exact real Caddy 2.6 syntax without starting a listener.
    caddy_env={**os.environ,"ASSISTX_TAILNET_IP":str(addr)}
    caddy=subprocess.run(["/usr/bin/caddy","validate","--config",str(CADDYFILE),
                          "--adapter","caddyfile"],
                         capture_output=True,text=True,env=caddy_env,timeout=12)
    must(caddy.returncode==0,"Caddy rejected staged production config")
    evidence["real_caddy_syntax_validated"]=True
    evidence["tailnet_port_8000_replacement_compatible_on_wire"]="not_yet_proven"
    evidence["expected_live_api_pinned"]=True
    return evidence

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-readonly",action="store_true")
    args=parser.parse_args()
    print(json.dumps(physical() if args.physical_readonly else
                     {"decision":"HOLD","physical_executed":False,
                      "automatic_cutover_authorized":False,
                      "production_mutations":0},sort_keys=True))
