#!/usr/bin/env python3
"""Opt-in off-host Tailnet Basic-auth read-only canary via pinned SSH SOCKS.

Secrets remain in x1-370 process memory; no real client configuration is
changed. Lenovo only relays TCP; the ephemeral Tailnet listener is guarded
to GET /health or GET /control-room. Never touches production port 8000.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from assistx_tailnet_lenovo_ingress_canary import tailnet_address
from assistx_tailnet_real_basic_canary import exact_live_state, get_basic_in_memory
from assistx_tailnet_sameport_rollback_canary import (
    RELEASE_CADDYFILE,
    REVIEWED_CADDYFILE_SHA256,
)
from assistx_tailnet_sanitizer_canary import DROPPED_HEADERS, free_port


class Refused(RuntimeError):
    pass

def must(ok,reason):
    if not ok:
        raise Refused(reason)

def command(args,timeout=10):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=False)

def release_config(tailnet_ip,port):
    ip=ipaddress.ip_address(tailnet_ip)
    must(ip.version==4 and ip in ipaddress.ip_network("100.64.0.0/10"),
         "invalid Tailnet listener")
    must(type(port) is int and 1025<port<65536 and port!=8000,
         "refusing production port")
    must(RELEASE_CADDYFILE.is_file() and not RELEASE_CADDYFILE.is_symlink(),
         "reviewed release file unavailable")
    raw=RELEASE_CADDYFILE.read_bytes()
    must(hashlib.sha256(raw).hexdigest()==REVIEWED_CADDYFILE_SHA256,
         "unreviewed release bytes")
    cfg=raw.decode("utf-8")
    listener="http://{$ASSISTX_TAILNET_IP}:8000 {"
    proxy="    reverse_proxy http://127.0.0.1:8000 {"
    must(cfg.count(listener)==1 and cfg.count(proxy)==1
         and cfg.count("reverse_proxy")==1
         and cfg.endswith("    }\n}\n"),
         "unexpected release structure")
    for name in DROPPED_HEADERS:
        must(cfg.count("header_up -"+name)==1,
             "release missing header removal")
    cfg=cfg.replace(listener,f"http://{ip}:{port} {{",1)
    cfg=cfg.replace(proxy,
        "    @readonly {\n        method GET\n"
        "        path /health /control-room\n    }\n"
        "    handle @readonly {\n"
        "        reverse_proxy http://127.0.0.1:8000 {",1)
    cfg=cfg[:-len("    }\n}\n")]+(
        "        }\n    }\n    handle {\n"
        '        respond "Denied" 404\n    }\n}\n')
    must("{$ASSISTX_TAILNET_IP}" not in cfg
         and cfg.count("reverse_proxy")==1
         and cfg.count('respond "Denied" 404')==1,
         "guarded Tailnet Caddy configuration invalid")
    return cfg

def exact(sock,n):
    out=b""
    while len(out)<n:
        block=sock.recv(n-len(out))
        must(bool(block),"unexpected SOCKS EOF")
        out+=block
    return out

def socks_connect(proxy_port,remote_ip,remote_port):
    must(type(proxy_port) is int and 1025<proxy_port<65536,
         "unexpected SOCKS listener port")
    ip=ipaddress.ip_address(remote_ip)
    must(ip.version==4 and ip in ipaddress.ip_network("100.64.0.0/10")
         and type(remote_port) is int and 1025<remote_port<65536
         and remote_port!=8000,
         "unexpected SOCKS target")
    sock=socket.create_connection(("127.0.0.1",proxy_port),timeout=4)
    try:
        sock.settimeout(4)
        sock.sendall(b"\x05\x01\x00")
        must(exact(sock,2)==b"\x05\x00","SOCKS5 handshake rejected")
        sock.sendall(b"\x05\x01\x00\x01"+ip.packed+struct.pack("!H",remote_port))
        head=exact(sock,4)
        must(head[0]==5 and head[1]==0 and head[2]==0,
             "SSH SOCKS remote TCP connection refused")
        length={1:4,4:16}.get(head[3])
        if length is None:
            must(head[3]==3,"unexpected SOCKS reply address")
            length=exact(sock,1)[0]
        exact(sock,length+2)
        return sock
    except BaseException:
        sock.close()
        raise

def read_only_status(proxy_port,ip,port,basic=None,forged=False,path="/control-room"):
    must(path in ("/health","/control-room"),"only approved GET paths")
    sock=socks_connect(proxy_port,ip,port)
    try:
        header=(f"GET {path} HTTP/1.1\r\nHost: {ip}:{port}\r\n"
                "Connection: close\r\nUser-Agent: offhost-readonly-proof/1\r\n")
        if basic is not None:
            must(basic.startswith("Basic "),"only pinned Basic scheme supported")
            header+="Authorization: "+basic+"\r\n"
        if forged:
            header+="Tailscale-User-Login: synthetic-untrusted-identity\r\n"
            header+="X-Forwarded-User: synthetic-untrusted-identity\r\n"
        sock.sendall((header+"\r\n").encode("ascii"))
        line=bytearray()
        while len(line)<128:
            b=sock.recv(1)
            must(bool(b),"HTTP status response missing")
            line.extend(b)
            if line.endswith(b"\r\n"):
                break
        must(line.endswith(b"\r\n"),"HTTP status too long")
        words=bytes(line).split()
        must(len(words)>=2 and words[0].startswith(b"HTTP/"),
             "not an HTTP status line")
        return int(words[1])
    finally:
        sock.close()

def remote_tailnet():
    p=command(["ssh","-n","-T","-o","BatchMode=yes",
               "-o","StrictHostKeyChecking=yes","-o","ConnectTimeout=5",
               "-o","ConnectionAttempts=1","lenovo","tailscale ip -4"],9)
    must(p.returncode==0 and len(p.stdout.strip().splitlines())==1,
         "Lenovo Tailnet identity unavailable")
    addr=ipaddress.ip_address(p.stdout.strip())
    must(addr.version==4 and addr in ipaddress.ip_network("100.64.0.0/10"),
         "Lenovo not on Tailnet")
    return str(addr)

def start_tunnel(proxy_port):
    cmd=["ssh","-n","-N","-T",
         "-o","BatchMode=yes","-o","StrictHostKeyChecking=yes",
         "-o","ConnectTimeout=5","-o","ExitOnForwardFailure=yes",
         "-o","ServerAliveInterval=3","-o","ServerAliveCountMax=1",
         "-D",f"127.0.0.1:{proxy_port}","lenovo"]
    return subprocess.Popen(cmd,stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                 start_new_session=True)

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

def prove_peer(proxy_port,local_tailnet_ip,expected_peer):
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listener.settimeout(6)
    listener.bind((local_tailnet_ip,0))
    listener.listen(1)
    proof_port=listener.getsockname()[1]
    seen={}
    def accept_once():
        try:
            conn,addr=listener.accept()
            with conn:
                conn.settimeout(3)
                seen["peer"]=addr[0]
                if conn.recv(8)==b"ping":
                    conn.sendall(b"pong")
        except OSError:
            seen["peer"]="invalid"
    thread=threading.Thread(target=accept_once,daemon=True)
    thread.start()
    try:
        with socks_connect(proxy_port,local_tailnet_ip,proof_port) as sock:
            sock.sendall(b"ping")
            must(exact(sock,4)==b"pong","remote source challenge failed")
        thread.join(timeout=3)
        must(not thread.is_alive() and seen.get("peer")==expected_peer,
             "SOCKS request did not egress from pinned Lenovo Tailnet IP")
        return True
    finally:
        listener.close()
        thread.join(timeout=1)

def physical():
    must(os.uname().nodename=="x1-370" and os.geteuid()==1000,
         "nonroot x1-370 only")
    api,hermes,proxy=exact_live_state()
    basic=get_basic_in_memory(api)
    host=tailnet_address()
    peer=remote_tailnet()
    gateway_port=free_port()
    proxy_port=free_port()
    must(gateway_port!=proxy_port and gateway_port!=8000
         and proxy_port!=8000,"ephemeral port collision")
    checks={}
    caddy=None
    ssh=None
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-lenovo-basic-canary-") as work:
            root=Path(work)
            root.chmod(0o700)
            config=root/"Caddyfile"
            config.write_text(release_config(host,gateway_port))
            check=command(["/usr/bin/caddy","validate","--config",str(config),
                           "--adapter","caddyfile"],10)
            must(check.returncode==0,"disposable Caddy config invalid")
            checks["pinned_release_guard_valid"]=True
            caddy=subprocess.Popen(["/usr/bin/caddy","run","--config",str(config),
                                    "--adapter","caddyfile"],
                     stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,start_new_session=True)
            ssh=start_tunnel(proxy_port)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                must(caddy.poll() is None and ssh.poll() is None,
                     "temporary listener exited early")
                try:
                    if read_only_status(proxy_port,host,gateway_port,
                                        path="/health")==200:
                        break
                except (OSError,Refused,ValueError):
                    pass
                time.sleep(0.15)
            else:
                raise Refused("Tailnet sanitized SOCKS route unavailable")
            checks["lenovo_socks_tailnet_health_200"]=True
            checks["lenovo_remote_source_confirmed"]=prove_peer(proxy_port,host,peer)
            unauth=read_only_status(proxy_port,host,gateway_port)
            forged=read_only_status(proxy_port,host,gateway_port,forged=True)
            valid=read_only_status(proxy_port,host,gateway_port,basic=basic)
            mixed=read_only_status(proxy_port,host,gateway_port,basic=basic,forged=True)
            must((unauth,forged,valid,mixed)==(401,401,200,200),
                 "off-host Basic or forged-header status differed")
            checks["lenovo_anonymous_get_denied"]=True
            checks["lenovo_forged_identity_denied"]=True
            checks["lenovo_real_basic_get_accepted"]=True
            checks["lenovo_basic_plus_forgery_sanitized"]=True
    finally:
        stop(ssh)
        stop(caddy)
    checks["temporary_ssh_and_caddy_cleaned"]=(ssh is not None
        and caddy is not None and ssh.poll() is not None and caddy.poll() is not None)
    later_api,later_hermes,later_proxy=exact_live_state()
    checks["running_api_hermes_proxy_unchanged"]=(
        later_api["Image"]==api["Image"] and later_hermes==hermes
        and later_proxy==proxy)
    must(all(checks.values()),"off-host evidence incomplete")
    return {"decision":"HOLD","passed":sum(checks.values()),"checks":checks,
            "lenovo_origin_qualified":True,"credentials_written_to_lenovo":False,
            "local_client_held_real_basic_in_memory":True,
            "protected_response_bodies_read":False,
            "production_task_writes":0,"live_cutover_authorized":False}

if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--physical-lenovo-socks-basic",action="store_true")
    options=cli.parse_args()
    report=physical() if options.physical_lenovo_socks_basic else {
        "decision":"HOLD","physical_executed":False,"live_cutover_authorized":False}
    print(json.dumps(report,sort_keys=True))
