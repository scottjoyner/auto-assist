#!/usr/bin/env python3
"""Read-only ingress/persistence release fence for AssistX Tailnet port 8000.

Never changes systemd, Docker, Caddy, Tailscale or firewall state. The only
reported disposition is HOLD, even when static indicators appear healthy:
a real cutover and independently authenticated acceptance are still required.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CADDYFILE=ROOT/"scripts/assistx-tailnet-header-sanitizer.Caddyfile"
EXPECTED_CADDYFILE_SHA="c4136364469a45f918c8e9a33ce5268f57dc4ee39b030012af4f66aaf9b186b2"
EXPECTED_CADDY_BINARY_SHA="9681d054b6e5137d53f6d027188b349e0603fd12ea6334bd0d49da6755432f1b"
SERVICE="control-room-ts-proxy.service"

class ObservationFailed(RuntimeError):
    pass

def require(ok,reason):
    if not ok:
        raise ObservationFailed(reason)

def command(args,timeout=9,allow_failure=False):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if not allow_failure:
        require(result.returncode==0,"read-only observation failed")
    return result

def sha256_file(path):
    require(path.is_file() and not path.is_symlink(),"reviewed file missing")
    return hashlib.sha256(path.read_bytes()).hexdigest()

def listen_endpoints(ss_text):
    endpoints=[]
    for line in ss_text.splitlines():
        fields=line.split()
        require(len(fields)>=5,"unexpected socket listing")
        if fields[0]=="LISTEN":
            endpoints.append(fields[3])
    return endpoints

def evaluate(*,raw_active,raw_enabled,raw_unit_matches,
             backend_loopback_only,tailnet_listener,local_listener,
             unknown_8000_listener,caddyfile_pinned,binary_pinned,
             serve_api_proxy_routes):
    require(type(serve_api_proxy_routes) is int and serve_api_proxy_routes>=0,
            "invalid observed Serve route count")
    if not backend_loopback_only or unknown_8000_listener:
        issue="UNKNOWN_EXPOSURE"
    elif raw_active:
        issue=("RAW_FORWARDER_BOOT_PERSISTENT" if raw_enabled
               else "RAW_FORWARDER_CURRENTLY_ACTIVE")
    elif raw_enabled:
        issue="UNSAFE_SOCAT_RESTART_ON_BOOT"
    elif not tailnet_listener:
        issue="FAIL_CLOSED_NO_TAILNET_INGRESS"
    else:
        issue="CADDY_INGRESS_NOT_YET_AUTHENTICATED"
    return {
        "decision":"HOLD",
        "reason":issue,
        "legacy_socat_running":raw_active,
        "legacy_socat_enabled_at_boot":raw_enabled,
        "legacy_unit_signature_expected":raw_unit_matches,
        "backend_only_listens_on_loopback":backend_loopback_only,
        "tailnet_port_8000_present":tailnet_listener,
        "local_api_port_8000_present":local_listener,
        "unexpected_port_8000_listener":unknown_8000_listener,
        "reviewed_caddyfile_sha_verified":caddyfile_pinned,
        "pinned_caddy_binary_sha_verified":binary_pinned,
        "separate_tailscale_serve_api_routes":serve_api_proxy_routes,
        "postcutover_real_client_acceptance":False,
        "sanitizing_fallback_independently_qualified":False,
        "automatic_cutover_or_rollback_authorized":False,
        "production_mutations":0,
        "credential_values_collected":False,
    }

def physical():
    import os
    require(os.uname().nodename=="x1-370" and os.geteuid()==1000,
            "read-only x1-370 nonroot only")
    ip_result=command(["tailscale","ip","-4"],6)
    addresses=ip_result.stdout.strip().splitlines()
    require(len(addresses)==1,"unexpected Tailnet identity")
    ip=ipaddress.ip_address(addresses[0])
    require(ip.version==4 and ip in ipaddress.ip_network("100.64.0.0/10"),
            "unverified Tailnet IPv4")
    active=command(["systemctl","--user","is-active",SERVICE],
                   allow_failure=True).stdout.strip()=="active"
    enabled=command(["systemctl","--user","is-enabled",SERVICE],
                    allow_failure=True).stdout.strip() in ("enabled","enabled-runtime")
    service=command(["systemctl","--user","show",SERVICE,
                     "--property=ExecStart"],8)
    signature=(f"TCP-LISTEN:8000,bind={ip},fork,reuseaddr" in service.stdout
               and "TCP:127.0.0.1:8000" in service.stdout
               and "/usr/bin/socat" in service.stdout)
    sockets=listen_endpoints(command(["ss","-H","-lnt"],8).stdout)
    ports=[s for s in sockets if s.rsplit(":",1)[-1]=="8000"]
    expected={f"{ip}:8000","127.0.0.1:8000"}
    unknown=any(s not in expected for s in ports)
    docker=command(["docker","inspect","-f",
                    "{{json .NetworkSettings.Ports}}","assistx-api"],8)
    bindings=json.loads(docker.stdout)
    only_loopback=(bindings.get("8000/tcp")==[
        {"HostIp":"127.0.0.1","HostPort":"8000"}])
    serve=command(["tailscale","serve","status","--json"],9)
    serving=json.loads(serve.stdout)
    count=sum(1 for web in serving.get("Web",{}).values()
              for handler in web.get("Handlers",{}).values()
              if "127.0.0.1:8000" in (handler.get("Proxy") or ""))
    result=evaluate(
        raw_active=active,raw_enabled=enabled,raw_unit_matches=signature,
        backend_loopback_only=only_loopback,
        tailnet_listener=f"{ip}:8000" in ports,
        local_listener="127.0.0.1:8000" in ports,
        unknown_8000_listener=unknown,
        caddyfile_pinned=(sha256_file(CADDYFILE)==EXPECTED_CADDYFILE_SHA),
        binary_pinned=(sha256_file(Path("/usr/bin/caddy"))==EXPECTED_CADDY_BINARY_SHA),
        serve_api_proxy_routes=count)
    require(result["decision"]=="HOLD" and result["production_mutations"]==0,
            "release fence unexpectedly not holding")
    return result

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-readonly",action="store_true")
    args=parser.parse_args()
    print(json.dumps(physical() if args.physical_readonly else {
        "decision":"HOLD","physical_executed":False,
        "automatic_cutover_or_rollback_authorized":False,
        "production_mutations":0},sort_keys=True))
