#!/usr/bin/env python3
"""Read-only, post-switch ingress attestation. Never deploys or restores proxy.

A successful static candidate inspection is not client-authentication proof
and never authorizes Hermes/AssistX promotion. Inert unless explicitly asked.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import subprocess
from pathlib import Path

EXPECTED_UNIT="assistx-tailnet-sanitizer.service"
OLD_UNIT="control-room-ts-proxy.service"
EXPECTED_BIN=Path("/usr/bin/caddy")
EXPECTED_CONFIG=Path("/home/scott/.config/assistx-tailnet-sanitizer/Caddyfile")
EXPECTED_BIN_SHA="9681d054b6e5137d53f6d027188b349e0603fd12ea6334bd0d49da6755432f1b"
EXPECTED_CONFIG_SHA="c4136364469a45f918c8e9a33ce5268f57dc4ee39b030012af4f66aaf9b186b2"
CANDIDATE_UNITS={
    "primary":(EXPECTED_UNIT,EXPECTED_CONFIG,
               "c4e05793aeb266e8636ca108c03101bda2cb852e10952312bd6bb733374ab887"),
    "fallback":("assistx-tailnet-sanitizer-fallback.service",
                Path("/home/scott/.config/assistx-tailnet-sanitizer/fallback.Caddyfile"),
                "dd73f26042f1626a19875a32532f90fe539617d0778ba1e4eaf9c74fa181e263"),
}
USER_UNIT_DIR=Path("/home/scott/.config/systemd/user")


class Unsafe(ValueError):
    pass


def require(ok,reason):
    if not ok:
        raise Unsafe(reason)


def command(args,timeout=8):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout)


def is_pinned_file(path,sha):
    try:
        if not path.is_file() or path.is_symlink():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest()==sha
    except OSError:
        return False


def service_owns_listener(ss_text, listen_addr, unit_cgroup, uid=1000):
    """Bind one TCP listener to an exact systemd cgroup, UID and socket inode."""
    if (not isinstance(ss_text,str) or not isinstance(listen_addr,str)
            or not isinstance(unit_cgroup,str)
            or not unit_cgroup.startswith("/user.slice/")
            or not isinstance(uid,int)):
        return False
    matches=[]
    for row in ss_text.splitlines():
        columns=row.split()
        if len(columns)<5 or columns[0]!="LISTEN" or columns[3]!=listen_addr:
            continue
        labels={}
        for token in columns[5:]:
            if ":" not in token:
                continue
            key,value=token.split(":",1)
            if key in ("uid","ino","cgroup"):
                if key in labels:
                    return False
                labels[key]=value
        matches.append(labels)
    if len(matches)!=1:
        return False
    labels=matches[0]
    return (labels.get("uid")==str(uid)
            and labels.get("cgroup")==unit_cgroup
            and labels.get("ino","").isdigit()
            and int(labels["ino"])>0)


MOBILE_PATHS=frozenset((
    "/health","/api/v1/auth/whoami","/api/v1/runtime/catalog",
    "/api/v1/agent/chat/completions","/api/v1/model/chat/completions",
))


def mobile_serve_paths_exact(web,hostname):
    """Require the five approved paths at the exact verified Tailnet DNS name."""
    if (type(web) is not dict or type(hostname) is not str
            or not hostname.endswith(".ts.net")):
        return False
    expected_host=hostname+":8443"
    routes=set()
    for host,config in web.items():
        if type(host) is not str or type(config) is not dict:
            return False
        entries=config.get("Handlers")
        if type(entries) is not dict:
            return False
        for path,value in entries.items():
            if type(path) is not str or type(value) is not dict:
                return False
            proxy=value.get("Proxy")
            if type(proxy) is not str:
                if host==expected_host and path in MOBILE_PATHS:
                    return False
                continue
            if proxy.startswith("http://127.0.0.1:8000"):
                if (host!=expected_host or path not in MOBILE_PATHS
                        or proxy!="http://127.0.0.1:8000"+path
                        or path in routes):
                    return False
                routes.add(path)
            elif host==expected_host and path in MOBILE_PATHS:
                return False
    return routes==MOBILE_PATHS


def process_identity(pid):
    if not 1<=pid<=4194304:
        return False
    try:
        proc=Path("/proc")/str(pid)
        executable=(proc/"exe").resolve(strict=True)
        argv=(proc/"cmdline").read_bytes().split(b"\0")
        expected=(str(EXPECTED_BIN).encode(),b"run",b"--config",
                  str(EXPECTED_CONFIG).encode(),b"--adapter",b"caddyfile")
        return executable==EXPECTED_BIN.resolve(strict=True) and tuple(argv[:-1])==expected
    except (OSError,ValueError):
        return False


def systemd_execution_identity(unit,config,unit_sha,fields):
    """Trust exact pinned user-unit bytes and manager ExecStart, not /proc access.

    Live socket UID/cgroup/inode is a SEPARATE mandatory predicate. A unit
    definition by itself is never evidence that it owns a live TCP listener.
    """
    if (not isinstance(fields,dict) or not isinstance(unit,str)
            or not isinstance(config,Path) or not isinstance(unit_sha,str)):
        return False,False
    fragment=USER_UNIT_DIR/unit
    unit_bytes_pinned=(is_pinned_file(fragment,unit_sha)
                       and fields.get("FragmentPath")==str(fragment))
    expected=("{ path=/usr/bin/caddy ; argv[]=/usr/bin/caddy run --config "
              +str(config)+" --adapter caddyfile ; ignore_errors=no ;")
    exec_value=fields.get("ExecStart")
    pid_text=fields.get("MainPID")
    exec_pinned=(isinstance(exec_value,str) and exec_value.startswith(expected)
                 and isinstance(pid_text,str) and pid_text.isdigit()
                 and int(pid_text)>0
                 and fields.get("ControlGroup","").endswith("/"+unit))
    return unit_bytes_pinned,exec_pinned


def assess(*,legacy_active,legacy_boot_enabled,caddy_active,caddy_boot_enabled,
           caddy_process_pinned,caddy_config_pinned,caddy_binary_pinned,
           tailnet_listener,localhost_listener,unexpected_listener,
           tailnet_socket_owned_by_caddy,mobile_serve_scope_exact,
           systemd_unit_pinned=False,systemd_execstart_pinned=False,
           legacy_unit_masked=False,real_offhost_auth_verified=False):
    booleans=(legacy_active,legacy_boot_enabled,caddy_active,caddy_boot_enabled,
              caddy_process_pinned,caddy_config_pinned,caddy_binary_pinned,
              tailnet_listener,localhost_listener,unexpected_listener,
              tailnet_socket_owned_by_caddy,mobile_serve_scope_exact,
              systemd_unit_pinned,systemd_execstart_pinned,
              legacy_unit_masked,real_offhost_auth_verified)
    require(all(type(item) is bool for item in booleans),"nonboolean observations")
    static=(not legacy_active and not legacy_boot_enabled and legacy_unit_masked
            and caddy_active
            and caddy_boot_enabled
            and (caddy_process_pinned or
                 (systemd_unit_pinned and systemd_execstart_pinned))
            and caddy_config_pinned and caddy_binary_pinned
            and tailnet_listener and localhost_listener
            and tailnet_socket_owned_by_caddy
            and not unexpected_listener and mobile_serve_scope_exact)
    return {
        "decision":"HOLD",
        "postcutover_static_gate_met":static,
        "legacy_unit_inactive":not legacy_active,
        "legacy_unit_cannot_start_at_boot":not legacy_boot_enabled,
        "legacy_unit_masked":legacy_unit_masked,
        "pinned_caddy_process_verified":caddy_process_pinned,
        "pinned_systemd_unit_verified":systemd_unit_pinned,
        "pinned_systemd_execstart_verified":systemd_execstart_pinned,
        "process_identity_evidence_source":(
            "procfs_executable_and_argv" if caddy_process_pinned else
            "pinned_systemd_unit_execstart_and_socket_cgroup"
            if (systemd_unit_pinned and systemd_execstart_pinned
                and tailnet_socket_owned_by_caddy)
            else "unverified"),
        "tailnet_socket_owned_by_caddy":tailnet_socket_owned_by_caddy,
        "pinned_caddy_config_verified":caddy_config_pinned,
        "mobile_serve_scope_exact":mobile_serve_scope_exact,
        "real_offhost_auth_verified":real_offhost_auth_verified,
        "safe_fallback_activated_and_qualified":False,
        "production_release_authorized":False,
        "production_mutations":0,
    }


def physical(candidate="primary"):
    require(candidate in CANDIDATE_UNITS,"unknown approved Caddy candidate")
    unit,config,unit_sha=CANDIDATE_UNITS[candidate]
    require(os.uname().nodename=="x1-370" and os.geteuid()==1000,
            "read-only nonroot x1-370 only")
    ipresult=command(["tailscale","ip","-4"])
    require(ipresult.returncode==0 and len(ipresult.stdout.splitlines())==1,
            "Tailnet interface not uniquely identified")
    ip=ipaddress.ip_address(ipresult.stdout.strip())
    require(ip.version==4 and ip in ipaddress.ip_network("100.64.0.0/10"),
            "unverified Tailnet IPv4")
    def state(unit):
        active=command(["systemctl","--user","is-active",unit]).stdout.strip()=="active"
        enabled=command(["systemctl","--user","is-enabled",unit]).stdout.strip()
        return active,enabled in ("enabled","enabled-runtime")
    raw,raw_boot=state(OLD_UNIT)
    old_mask=(command(["systemctl","--user","is-enabled",OLD_UNIT])
              .stdout.strip()=="masked"
              and "LoadState=masked" in command([
                  "systemctl","--user","show",OLD_UNIT,"-p","LoadState"]).stdout)
    new,new_boot=state(unit)
    snapshot=command(["systemctl","--user","show",unit,
                      "--property=MainPID,ControlGroup,FragmentPath,ExecStart"])
    values=dict(x.split("=",1) for x in snapshot.stdout.splitlines() if "=" in x)
    pid=int(values.get("MainPID") or "0")
    unit_pinned,exec_pinned=systemd_execution_identity(unit,config,unit_sha,values)
    ss=command(["ss","-H","-lntpe"])
    require(ss.returncode==0,"listener snapshot unavailable")
    ports=[row.split()[3] for row in ss.stdout.splitlines()
           if len(row.split())>=5 and row.split()[3].endswith(":8000")]
    expected={f"{ip}:8000","127.0.0.1:8000"}
    owned=service_owns_listener(ss.stdout,f"{ip}:8000",
                                values.get("ControlGroup",""))
    serve=command(["tailscale","serve","status","--json"])
    require(serve.returncode==0,"Serve inventory unavailable")
    web=json.loads(serve.stdout).get("Web",{})
    self_status=command(["tailscale","status","--json"])
    require(self_status.returncode==0,"Tailnet DNS inventory unavailable")
    name=str((json.loads(self_status.stdout).get("Self") or {}).get("DNSName") or "").strip().rstrip(".")
    approved_mobile_routes=mobile_serve_paths_exact(web,name)
    report=assess(
        legacy_active=raw,legacy_boot_enabled=raw_boot,
        caddy_active=new,caddy_boot_enabled=new_boot,
        caddy_process_pinned=(process_identity(pid) if candidate=="primary" else False),
        caddy_config_pinned=is_pinned_file(config,EXPECTED_CONFIG_SHA),
        caddy_binary_pinned=is_pinned_file(EXPECTED_BIN,EXPECTED_BIN_SHA),
        tailnet_listener=f"{ip}:8000" in ports,
        localhost_listener="127.0.0.1:8000" in ports,
        unexpected_listener=any(s not in expected for s in ports),
        tailnet_socket_owned_by_caddy=owned,
        mobile_serve_scope_exact=approved_mobile_routes,
        systemd_unit_pinned=unit_pinned,systemd_execstart_pinned=exec_pinned,
        legacy_unit_masked=old_mask)
    require(report["decision"]=="HOLD","postcutover gate unexpectedly promoted")
    return report


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--physical-readonly",action="store_true")
    cli.add_argument("--candidate",choices=("primary","fallback"),default="primary")
    args=cli.parse_args()
    print(json.dumps(physical(args.candidate) if args.physical_readonly else {
        "decision":"HOLD","physical_executed":False,
        "production_release_authorized":False,
        "production_mutations":0},sort_keys=True))
