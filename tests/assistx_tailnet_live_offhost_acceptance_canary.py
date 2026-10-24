#!/usr/bin/env python3
"""Live but read-only off-host authentication check for *verified* Caddy ingress.

Explicit --physical-live-readonly only. Authorizes a SOCKS tunnel to real
Tailnet port 8000 ONLY after verifying the old socat is disabled, the exact
pinned Caddy file/binary, kernel-owned Caddy listener, and preserved mobile
Serve routes. Uses real Basic credentials in x1-370 memory only. Lenovo merely
carries TCP over pinned SSH, and never receives credentials on its filesystem.
GET /health and /command-center only. No production task or model mutations.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"tests"),str(ROOT/"scripts")]
import assistx_tailnet_postcutover_attestation as attest  # noqa: E402
import assistx_tailnet_lenovo_socks_basic_canary as offhost  # noqa: E402

RAW="control-room-ts-proxy.service"
PRIMARY="assistx-tailnet-sanitizer.service"
FALLBACK="assistx-tailnet-sanitizer-fallback.service"
CONFIG_DIR=Path("/home/scott/.config/assistx-tailnet-sanitizer")
EXPECTED_SHA="c4136364469a45f918c8e9a33ce5268f57dc4ee39b030012af4f66aaf9b186b2"
BIN_SHA="9681d054b6e5137d53f6d027188b349e0603fd12ea6334bd0d49da6755432f1b"
API_SHA="sha256:74df40c5c720dbef283f83eaf69eae585212748ca3889be508eaa9c88cd5b4c4"
HERMES_SHA="sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307"

class Denied(RuntimeError):
    pass

def require(ok,why):
    if not ok:
        raise Denied(why)

def run(args,timeout=8):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout)

def service_state(unit):
    status=run(["systemctl","--user","show",unit,
        "--property=ActiveState,UnitFileState,MainPID,ControlGroup"])
    require(status.returncode==0,"systemd metadata unavailable")
    return dict(item.split("=",1) for item in status.stdout.splitlines() if "=" in item)

def digest(p):
    require(p.is_file() and not p.is_symlink(),"pinned file missing")
    return hashlib.sha256(p.read_bytes()).hexdigest()

def trusted_ingress_state():
    """No sockets or production credentials used until this gate passes."""
    require(os.uname().nodename=="x1-370" and os.geteuid()==1000,
            "non-root x1-370 only")
    raw=service_state(RAW)
    require(raw.get("ActiveState")!="active"
            and raw.get("UnitFileState") not in ("enabled","enabled-runtime"),
            "raw socat still active or boot-enabled")
    choices=[]
    for unit,file in ((PRIMARY,"Caddyfile"),
                      (FALLBACK,"fallback.Caddyfile")):
        state=service_state(unit)
        if state.get("ActiveState")=="active":
            choices.append((unit,file,state))
    require(len(choices)==1,"not exactly one active Caddy ingress unit")
    unit,config_name,detail=choices[0]
    require(detail.get("UnitFileState") in ("enabled","enabled-runtime"),
            "Caddy service not boot-enabled")
    require(digest(Path("/usr/bin/caddy"))==BIN_SHA,
            "live binary does not match released Caddy")
    config=CONFIG_DIR/config_name
    require(digest(config)==EXPECTED_SHA,
            "active config differs from pinned identity sanitizer")
    pid=int(detail.get("MainPID") or "0")
    require(1 < pid < 4194304,"invalid active service PID")
    proc=Path("/proc")/str(pid)
    expected=(b"/usr/bin/caddy",b"run",b"--config",
              str(config).encode(),b"--adapter",b"caddyfile")
    # /proc/<pid>/exe can be inaccessible under protected user namespaces,
    # even for the same operator. Require exact running argv plus unit's
    # ExecStart, pinned on-disk executable, and later kernel socket cgroup.
    cmdline=tuple((proc/"cmdline").read_bytes().split(b"\0")[:-1])
    comm=(proc/"comm").read_text().strip()
    unit_exec=run(["systemctl","--user","show",unit,"--property=ExecStart"])
    require(unit_exec.returncode==0
            and "path=/usr/bin/caddy" in unit_exec.stdout
            and ("argv[]="+str(expected[0].decode())+" run --config "
                 +str(config)+" --adapter caddyfile") in unit_exec.stdout
            and cmdline==expected and comm=="caddy",
            "running argv or pinned systemd unit not exact Caddy")
    result=run(["tailscale","ip","-4"])
    require(result.returncode==0 and len(result.stdout.strip().splitlines())==1,
            "no unique Tailnet address")
    ip=ipaddress.ip_address(result.stdout.strip())
    require(ip.version==4 and str(ip)=="100.64.43.123",
            "Tailnet address changed")
    sockets=run(["ss","-H","-lntpe"])
    require(sockets.returncode==0,"kernel socket proof unavailable")
    require(attest.service_owns_listener(
        sockets.stdout,f"{ip}:8000",detail.get("ControlGroup","")),
        "Tailnet port 8000 not owned by exact active Caddy cgroup")
    require(not any(
        len(line.split())>=4 and line.split()[3] not in
            {f"{ip}:8000","127.0.0.1:8000"}
        for line in sockets.stdout.splitlines()
        if len(line.split())>=4 and line.split()[3].endswith(":8000")),
        "extra unsafe listener present on port 8000")
    serve=run(["tailscale","serve","status","--json"])
    status=run(["tailscale","status","--json"])
    require(serve.returncode==status.returncode==0,
            "separate mobile Serve inventory unavailable")
    web=json.loads(serve.stdout).get("Web",{})
    dns=((json.loads(status.stdout).get("Self") or {}).get("DNSName") or "")
    require(attest.mobile_serve_paths_exact(web,dns.rstrip(".")),
            "mobile Serve routes changed")
    return str(ip),unit

def basic_in_memory():
    response=run(["docker","inspect","--format","{{json .}}","assistx-api"])
    require(response.returncode==0 and len(response.stdout)<750000,
            "production API config unavailable")
    info=json.loads(response.stdout)
    require(info.get("Image")==API_SHA and
            info.get("State",{}).get("Running") is True,
            "API running image changed")
    env={}
    for item in info.get("Config",{}).get("Env",[]):
        name,separator,value=item.partition("=")
        require(separator=="=" and name not in env,
                "ambiguous API env")
        env[name]=value
    require(env.get("TRUSTED_AUTH_HEADER")=="Tailscale-User-Login",
            "unexpected identity header contract")
    user=env.get("BASIC_AUTH_USER","")
    password=env.get("BASIC_AUTH_PASS","")
    require(bool(user and password),"real Basic unavailable")
    return "Basic "+base64.b64encode((user+":"+password).encode()).decode()

def exact(sock,n):
    value=b""
    while len(value)<n:
        next_bytes=sock.recv(n-len(value))
        require(bool(next_bytes),"unexpected closed SSH tunnel")
        value+=next_bytes
    return value

def live_socket_through_lenovo(proxy_port,ip,*,verified_gate):
    """This is deliberately NOT the isolated-canary port selector.

    Production port is admitted only with the verified active Caddy gate.
    """
    require(verified_gate is True and ip=="100.64.43.123",
            "live SOCKS denied before verified sanitizer ownership")
    s=socket.create_connection(("127.0.0.1",proxy_port),timeout=4)
    try:
        s.settimeout(5)
        s.sendall(b"\x05\x01\x00")
        require(exact(s,2)==b"\x05\x00","SOCKS authentication negotiation failed")
        s.sendall(b"\x05\x01\x00\x01"+ipaddress.ip_address(ip).packed
                  +struct.pack("!H",8000))
        head=exact(s,4)
        require(head[0]==5 and head[1]==0 and head[2]==0,
                "Lenovo could not connect to approved Caddy port")
        kind=head[3]
        if kind==1:
            exact(s,6)
        elif kind==4:
            exact(s,18)
        elif kind==3:
            exact(s,exact(s,1)[0]+2)
        else:
            raise Denied("invalid SOCKS response")
        return s
    except BaseException:
        s.close()
        raise

def status_via_lenovo(proxy_port,ip,basic=None,forge=False,
                      path="/control-room",*,verified_gate=False):
    require(path in ("/health","/control-room"),
            "only audited read-only GET routes permitted")
    with live_socket_through_lenovo(proxy_port,ip,
                                   verified_gate=verified_gate) as conn:
        message=(f"GET {path} HTTP/1.1\r\nHost: {ip}:8000\r\n"
                 "Connection: close\r\nUser-Agent: sanitized-offhost-audit/1\r\n")
        if basic is not None:
            require(basic.startswith("Basic "),"unexpected credential type")
            message+="Authorization: "+basic+"\r\n"
        if forge:
            message+="Tailscale-User-Login: synthetic-invalid-user\r\n"
            message+="X-Forwarded-User: synthetic-invalid-user\r\n"
        conn.sendall((message+"\r\n").encode("ascii"))
        line=bytearray()
        while len(line)<128:
            item=conn.recv(1)
            require(bool(item),"no HTTP status")
            line+=item
            if line.endswith(b"\r\n"):
                break
        parts=bytes(line).split()
        require(len(parts)>=2 and parts[0].startswith(b"HTTP/"),
                "invalid HTTP response")
        return int(parts[1])

def physical():
    ip,unit=trusted_ingress_state()
    original=basic_in_memory()
    proxy_port=offhost.free_port()
    ssh=offhost.start_tunnel(proxy_port)
    try:
        deadline=time.monotonic()+9
        while time.monotonic()<deadline:
            require(ssh.poll() is None,"Lenovo SOCKS process stopped")
            try:
                if status_via_lenovo(
                    proxy_port,ip,path="/health",verified_gate=True)==200:
                    break
            except (OSError,Denied):
                time.sleep(0.15)
        else:
            raise Denied("Lenovo did not reach pinned sanitized listener")
        expected=(401,401,200,200)
        result=(
            status_via_lenovo(proxy_port,ip,verified_gate=True),
            status_via_lenovo(proxy_port,ip,forge=True,verified_gate=True),
            status_via_lenovo(proxy_port,ip,basic=original,verified_gate=True),
            status_via_lenovo(proxy_port,ip,basic=original,forge=True,
                              verified_gate=True),
        )
        require(result==expected,"live off-host auth or forgery check failed: HTTP statuses "+str(result))
    finally:
        offhost.stop(ssh)
    require(trusted_ingress_state()==(ip,unit),
            "Caddy ingress identity changed during verification")
    runtime=run(["docker","inspect","-f","{{.Image}} {{.State.Running}} {{len .Mounts}}",
                 "assistx-hermes-adapter"])
    require(runtime.returncode==0 and runtime.stdout.strip().split()==[
        HERMES_SHA,"true","16"],"Hermes runtime changed")
    return {"decision":"INGRESS_LIVE_ACCEPTED_HERMES_RELEASE_HOLD",
            "current_ingress":unit,
            "offhost_lenovo_health_200":True,
            "offhost_anonymous_401":True,
            "offhost_forged_401":True,
            "offhost_basic_200":True,
            "offhost_basic_plus_forgery_200":True,
            "kernel_socket_pinned_caddy":True,
            "old_socat_not_active_or_boot_enabled":True,
            "mobile_serve_scope_verified":True,
            "real_api_task_mutations":0,
            "private_credentials_logged_or_stored":False,
            "production_hermes_unchanged":True}

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-live-readonly",action="store_true")
    args=parser.parse_args()
    print(json.dumps(physical() if args.physical_live_readonly else {
        "decision":"HOLD","physical_executed":False,
        "real_api_task_mutations":0},sort_keys=True))
