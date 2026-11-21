#!/usr/bin/env python3
"""Opt-in physical Caddy test: duplicated/mixed-case forged identity headers.

Synthetic local backend and random localhost ports only. The Caddyfile is
derived from the SHA-pinned release bytes; neither the real API nor the
Tailnet production listener receives any request.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from assistx_tailnet_sameport_rollback_canary import staged_caddyfile
from assistx_tailnet_sanitizer_canary import DROPPED_HEADERS, free_port


class Denied(RuntimeError):
    pass


def need(ok, reason):
    if not ok:
        raise Denied(reason)


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        self.answer()

    def do_POST(self):
        self.answer()

    def do_PUT(self):
        self.answer()

    def answer(self):
        # get_all detects duplicate header instances, not merely first value.
        remaining=sum(len(self.headers.get_all(name, []))
                      for name in DROPPED_HEADERS)
        basic=self.headers.get_all("Authorization", [])
        record=json.dumps({
            "identity_header_instances":remaining,
            "basic_preserved":len(basic)==1 and basic[0].startswith("Basic "),
            "method":self.command,
        },sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(record)))
        self.end_headers()
        self.wfile.write(record)

    def log_message(self,*args):
        return


def raw_probe(port, method, pairs):
    need(method in ("GET","POST","PUT"),"unexpected synthetic method")
    need(type(port) is int and 1025<port<65536 and port!=8000,
         "not a disposable localhost port")
    need(all(k.lower() in {x.lower() for x in DROPPED_HEADERS}
             for k,v in pairs),"probe may only forge known identity headers")
    synthetic_basic="Basic "+base64.b64encode(b"fake-local:fake-local").decode()
    headers=[f"{method} /synthetic-only HTTP/1.1",
             f"Host: 127.0.0.1:{port}","Connection: close",
             "Authorization: "+synthetic_basic]
    headers.extend(f"{key}: {value}" for key,value in pairs)
    wire=("\r\n".join(headers)+"\r\n\r\n").encode("ascii")
    with socket.create_connection(("127.0.0.1",port),timeout=3) as client:
        client.settimeout(3)
        client.sendall(wire)
        response=bytearray()
        while len(response)<8192:
            piece=client.recv(8192-len(response))
            if not piece:
                break
            response.extend(piece)
    head,marker,body=bytes(response).partition(b"\r\n\r\n")
    need(marker and head.startswith(b"HTTP/1.1 200 ")
         or marker and head.startswith(b"HTTP/1.0 200 "),
         "Caddy/synthetic HTTP did not return success")
    result=json.loads(body)
    need(result.get("identity_header_instances")==0
         and result.get("basic_preserved") is True
         and result.get("method")==method,
         "duplicate or mixed-case identity survived proxy")
    return True


def cases():
    # Different spellings and duplicate keys test HTTP header canonicalization.
    return (
        ("GET",(("Tailscale-User-Login","synthetic-a"),)),
        ("GET",(("tailscale-user-login","synthetic-a"),
                ("TaIlScAlE-uSeR-LoGiN","synthetic-b"))),
        ("GET",tuple((header,"synthetic-"+str(i))
                     for i,header in enumerate(DROPPED_HEADERS))),
        ("POST",(("Tailscale-User-Login","synthetic-a"),
                 ("Tailscale-User-Login","synthetic-b"),
                 ("x-forwarded-user","synthetic-c"))),
        ("PUT",(("X-Authenticated-User","synthetic-a"),
                ("x-authenticated-user","synthetic-b"),
                ("remote-user","synthetic-c"))),
    )


def physical():
    need(os.uname().nodename=="x1-370" and os.geteuid()==1000,
         "x1-370 nonroot operator only")
    backend=ThreadingHTTPServer(("127.0.0.1",0),Echo)
    backend_port=backend.server_address[1]
    front=free_port()
    need(front not in (8000,backend_port),"disposable port collision")
    thread=threading.Thread(target=backend.serve_forever,daemon=True)
    thread.start()
    proc=None
    checks={}
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-dup-header-") as work:
            folder=Path(work)
            folder.chmod(0o700)
            config=folder/"Caddyfile"
            config.write_text(staged_caddyfile(backend_port,front))
            tested=subprocess.run(["/usr/bin/caddy","validate","--config",
                                   str(config),"--adapter","caddyfile"],
                                  capture_output=True,timeout=8)
            need(tested.returncode==0,"pinned Caddy derivative invalid")
            checks["pinned_caddyfile_valid"]=True
            proc=subprocess.Popen(["/usr/bin/caddy","run","--config",str(config),
                                   "--adapter","caddyfile"],
                                  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,start_new_session=True)
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                need(proc.poll() is None,"Caddy failed before probe")
                try:
                    raw_probe(front,*cases()[0])
                    break
                except (OSError,Denied,ValueError):
                    time.sleep(0.1)
            else:
                raise Denied("isolated Caddy did not start")
            checks["canonical_header_stripped"]=True
            for index,(method,pairs) in enumerate(cases()[1:],start=2):
                checks[f"forged_header_variant_{index}"]=raw_probe(front,method,pairs)
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=3)
    need(all(checks.values()),"incomplete identity header variant checks")
    return {"decision":"HOLD","checks":checks,"passed":len(checks),
            "production_network_requests":0,"production_service_mutations":0,
            "credentials_exposed":False,"production_cutover_authorized":False}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-loopback",action="store_true")
    args=parser.parse_args()
    print(json.dumps(physical() if args.physical_loopback else {
        "decision":"HOLD","physical_executed":False,
        "production_cutover_authorized":False},sort_keys=True))
