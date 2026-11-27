#!/usr/bin/env python3
"""Off-host Tailnet ingress qualification, disposable and opt-in only.

A short-lived Caddy binds a DIFFERENT high port on the x1-370 Tailnet IP,
allowing only read-only /health and /command-center; Lenovo performs bounded
GETs with synthetic forged identity headers. The live TCP:8000 socat unit,
production API/Hermes and real credentials are not changed.
This does not grant a production cutover or prove real-client compatibility.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

API_IMAGE = "sha256:74df40c5c720dbef283f83eaf69eae585212748ca3889be508eaa9c88cd5b4c4"
HERMES_IMAGE = "sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307"
CANARY_HEADERS = (
    "Tailscale-User-Login", "X-Forwarded-User",
    "X-Authenticated-User", "X-Remote-User",
    "Remote-User", "X-Forwarded-Email",
)
REMOTE_SCRIPT = r'''
import json,sys,urllib.error,urllib.request
host=sys.argv[1]
port=int(sys.argv[2])
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
results={}
for name,path,header in [
    ("health","/health",False),
    ("spoof","/control-room",True),
    ("blocked","/openapi.json",False),
]:
    headers={"User-Agent":"disposable-lenovo-readonly-ingress/1.0"}
    if header:
        headers["Tailscale-User-Login"]="synthetic-forgery-not-a-real-user"
        headers["X-Forwarded-User"]="synthetic-forgery-not-a-real-user"
    request=urllib.request.Request(
        "http://"+host+":"+str(port)+path,method="GET",headers=headers)
    try:
        with opener.open(request,timeout=3) as response:
            results[name]=response.status
    except urllib.error.HTTPError as exc:
        results[name]=exc.code
        exc.close()
    except (OSError,urllib.error.URLError,TimeoutError):
        results[name]=0
print(json.dumps(results,sort_keys=True))
'''

class QualificationDenied(RuntimeError):
    pass

def require(condition, reason):
    if not condition:
        raise QualificationDenied(reason)

def command(args:list[str],timeout:int=10):
    return subprocess.run(args,capture_output=True,
                          text=True,timeout=timeout,check=False)

def exact_runtime(container:str,image:str,mount_count:int)->str:
    p=command(["docker","inspect","-f","{{.Image}} {{.State.Running}} {{len .Mounts}}",
               container],10)
    expected=f"{image} true {mount_count}"
    require(p.returncode==0 and p.stdout.strip()==expected,
            "production image or mount witness changed")
    return expected

def tailnet_address():
    p=command(["tailscale","ip","-4"],5)
    require(p.returncode==0 and len(p.stdout.strip().splitlines())==1,
            "Tailnet identity ambiguous")
    ip=ipaddress.ip_address(p.stdout.strip())
    require(ip.version==4 and ip in ipaddress.ip_network("100.64.0.0/10"),
            "unexpected non-Tailnet address")
    return str(ip)

def choose_port(host:str):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind((host,0))
        port=sock.getsockname()[1]
    require(2000 < port < 65536 and port!=8000,
            "unsafe disposable listener port")
    return port

def caddyfile(host:str,port:int):
    ip=ipaddress.ip_address(host)
    require(ip.version==4 and ip in ipaddress.ip_network("100.64.0.0/10"),
            "cannot bind outside Tailnet")
    require(2000 < port < 65536 and port!=8000,
            "cannot bind production port")
    stripping="\n".join("            header_up -"+name for name in CANARY_HEADERS)
    return (
        "{\n    admin off\n    auto_https off\n}\n"
        f"http://{host}:{port} {{\n"

        "    handle /health {\n"
        "        reverse_proxy http://127.0.0.1:8000 {\n"
        +stripping+"\n        }\n    }\n"
        "    handle /command-center {\n"
        "        reverse_proxy http://127.0.0.1:8000 {\n"
        +stripping+"\n        }\n    }\n"
        "    handle /control-room {\n"
        "        reverse_proxy http://127.0.0.1:8000 {\n"
        +stripping+"\n        }\n    }\n"
        "    handle {\n        respond \"Denied\" 404\n    }\n}\n"
    )

def ready_local(host:str,port:int):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request=urllib.request.Request(f"http://{host}:{port}/health",method="GET")
    with opener.open(request,timeout=2) as response:
        return response.status==200

def remote_probe(ip:str,port:int)->dict:
    args=["python3","-B","-c",REMOTE_SCRIPT,ip,str(port)]
    remote=" ".join(shlex.quote(x) for x in args)
    p=command([
        "ssh","-n","-T","-o","BatchMode=yes",
        "-o","StrictHostKeyChecking=yes",
        "-o","ConnectTimeout=4","-o","ConnectionAttempts=1",
        "lenovo",remote],17)
    require(p.returncode==0 and len(p.stdout)<2000,
            "Lenovo could not complete bounded remote HTTP probe")
    result=json.loads(p.stdout)
    require(type(result) is dict and set(result)=={"health","spoof","blocked"}
            and all(type(x) is int for x in result.values()),
            "invalid remote status-only report")
    return result

def physical():
    require(os.uname().nodename=="x1-370" and os.geteuid()==1000,
            "x1-370 non-root scope only")
    require(Path("/usr/bin/caddy").is_file(),"required local Caddy binary absent")
    original_api=exact_runtime("assistx-api",API_IMAGE,10)
    original_hermes=exact_runtime("assistx-hermes-adapter",HERMES_IMAGE,16)
    service=command(["systemctl","--user","is-active",
                     "control-room-ts-proxy.service"],5)
    require(service.returncode==0 and service.stdout.strip()=="active",
            "production proxy unexpectedly stopped")
    ip=tailnet_address()
    port=choose_port(ip)
    checks={}
    proc=None
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-tailnet-lenovo-readonly-") as path:
            folder=Path(path)
            folder.chmod(0o700)
            config=folder/"Caddyfile"
            config.write_text(caddyfile(ip,port),encoding="utf-8")
            syntax=command(["/usr/bin/caddy","validate","--config",
                            str(config),"--adapter","caddyfile"],10)
            require(syntax.returncode==0,"temporary Caddyfile invalid")
            checks["disposable_tailnet_caddy_config_valid"]=True
            proc=subprocess.Popen([
                "/usr/bin/caddy","run","--config",str(config),
                "--adapter","caddyfile"],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,start_new_session=True)
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                require(proc.poll() is None,"temporary Caddy listener exited")
                try:
                    if ready_local(ip,port):
                        break
                except (OSError,urllib.error.URLError):
                    pass
                time.sleep(0.12)
            else:
                raise QualificationDenied("temporary Tailnet listener did not start")
            checks["local_tailnet_listener_health"]=True
            op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            local=urllib.request.Request(
                f"http://{ip}:{port}/control-room",method="GET",
                headers={"Tailscale-User-Login":"synthetic-forgery-not-a-real-user"})
            try:
                with op.open(local,timeout=3) as response:
                    local_status=response.status
            except urllib.error.HTTPError as error:
                local_status=error.code
                error.close()
            require(local_status==401,"sanitized local protected GET did not deny")
            answers=remote_probe(ip,port)
            require(answers=={"health":200,"spoof":401,"blocked":404},
                    "remote off-host response codes: "+json.dumps(answers,sort_keys=True))
            checks["lenovo_offhost_health_get_ok"]=True
            checks["lenovo_synthetic_identity_denied"]=True
            checks["lenovo_unlisted_api_route_not_exposed"]=True
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
    checks["temporary_caddy_listener_cleaned"]=True
    checks["running_api_unchanged"]=(
        exact_runtime("assistx-api",API_IMAGE,10)==original_api)
    checks["running_hermes_unchanged"]=(
        exact_runtime("assistx-hermes-adapter",HERMES_IMAGE,16)==original_hermes)
    active=command(["systemctl","--user","is-active",
                    "control-room-ts-proxy.service"],5)
    checks["production_socat_remained_active"]=(
        active.returncode==0 and active.stdout.strip()=="active")
    require(all(checks.values()),"physical test failed to close its proofs")
    return {
        "decision":"HOLD","checks":checks,"passed":sum(checks.values()),
        "production_task_mutations":0,
        "production_proxy_or_container_restarts":0,
        "actual_client_credentials_used":False,
        "independent_credential_custody_verified":False,
        "real_port_8000_cutover_authorized":False,
    }

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-tailnet-lenovo",action="store_true")
    args=parser.parse_args()
    report=physical() if args.physical_tailnet_lenovo else {
        "decision":"HOLD","physical_executed":False,
        "production_proxy_or_container_restarts":0,
        "real_port_8000_cutover_authorized":False,
    }
    print(json.dumps(report,sort_keys=True))
