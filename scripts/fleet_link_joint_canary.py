#!/usr/bin/env python3
"""Run an isolated two-repository AssistX -> Fleet Link -> mock llama canary.

Uses temporary, in-process private keys and loopback ephemeral listeners.
Does not connect to or alter live AssistX, Fleet Link, or model processes.
"""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from assistx.fleet_link_canary import build_fleet_link_canary_router
from assistx.mobile_agent_routes import _mobile_model_handle


class SyntheticLlama(BaseHTTPRequestHandler):
    def log_message(self, *args): return

    def send_doc(self, body):
        raw=json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            return self.send_doc({"status":"ok"})
        if self.path == "/props":
            return self.send_doc({"model_alias":"k2-1b"})
        self.send_error(404)

    def do_POST(self):
        length=int(self.headers["Content-Length"])
        body=json.loads(self.rfile.read(length))
        if self.path!="/v1/chat/completions" or body.get("model")!="k2-1b":
            self.send_error(404)
            return
        self.send_doc({"choices":[{
            "message":{"role":"assistant","content":"synthetic end-to-end success"},
            "finish_reason":"stop"}]})


def start(http_handler):
    srv=ThreadingHTTPServer(("127.0.0.1",0),http_handler)
    t=threading.Thread(target=srv.serve_forever,daemon=True)
    t.start()
    return srv


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--fleet-link-root",required=True,type=Path)
    args=ap.parse_args()
    root=args.fleet_link_root.resolve()
    source=root/"services"/"fleet_model_link.py"
    if not source.is_file():
        raise SystemExit("Fleet Link source not found")
    sys.path.insert(0,str(root/"services"))
    from fleet_model_link import Link,canonical,handler

    model=sidecar=None
    from unittest.mock import patch
    try:
        model=start(SyntheticLlama)
        signer=Ed25519PrivateKey.generate()
        public=signer.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        catalog={
            "schema":"fleet-model-link.v1","epoch":"synthetic",
            "generation":1,"expires_at":int(time.time()+600),
            "nodes":[{"id":"destroyer","peer_url":None}],
            "instances":[{"node":"destroyer","alias":"k2","upstream_id":"k2-1b",
                          "port":model.server_port,"kind":"chat",
                          "power_policy":"BATTERY_CANDIDATE"}],
        }
        snapshot={"manifest":catalog,"signature_hex":signer.sign(canonical(catalog)).hex()}
        link=Link(node_id="destroyer",manifest=snapshot,pinned_pubkey_hex=public)
        sidecar=start(handler(link))
        url=f"http://127.0.0.1:{sidecar.server_port}"
        provider_model={
            "alias":"K2","provider_model":"destroyer/k2",
            "artifact_fingerprint":"sha256:synthetic-artifact",
            "capabilities":["chat","local_only"]
        }
        import os
        env={
            "ASSISTX_FLEET_LINK_CANARY":"1",
            "ASSISTX_FLEET_LINK_URL":url,
            "ASSISTX_FLEET_LINK_RUNTIME_ID":"fleetlink:destroyer",
            "KIPNERTER_MOBILE_MODEL_HANDLE_SECRET":"ephemeral-integration-secret",
        }
        document={
            "schema_version":"2","source":"assistx",
            "expires_at_ms":int(time.time()*1000)+120000,
            "providers":[{
                "enabled":True,"node_id":"destroyer",
                "runtime_instance_id":"fleetlink:destroyer",
                "runtime_kind":"openai_compatible","type":"openai_compatible",
                "base_url":url+"/v1","access_urls":[url+"/v1"],
                "models":[provider_model]
            }]
        }
        api=FastAPI()
        api.include_router(build_fleet_link_canary_router(
            lambda: "authorized-operator", lambda: document))
        with patch.dict(os.environ,env):
            client=TestClient(api)
            preview=client.get("/api/fleet-link/preview")
            handle=_mobile_model_handle(provider_model)
            trace="00-"+"1"*32+"-"+"2"*16+"-01"
            reply=client.post("/api/fleet-link/canary",
                headers={"traceparent":trace},
                json={"model_handle":handle,"prompt":"short synthetic canary","max_tokens":32})
            assert preview.status_code==200,preview.text
            assert preview.json()["ready_model_count"]==1,preview.text
            assert reply.status_code==200,reply.text
            assert reply.json()["model"]==handle
            assert reply.json()["choices"][0]["message"]["content"]=="synthetic end-to-end success"
            assert reply.headers["X-Fleet-Trace-Id"]=="1"*32
            print(json.dumps({
                "schema":"assistx-fleet-link-joint-acceptance.v1",
                "result":"PASS","scope":"SYNTHETIC_LOOPBACK_ONLY",
                "projection_admission":"EXACT_RUNTIME_ROUTE_AND_ARTIFACT",
                "backend":"disposable_mock_llama",
                "streaming":False,
                "trace_id_preserved":True,
                "live_service_mutations":False
            },sort_keys=True))
    finally:
        for srv in (sidecar,model):
            if srv:
                srv.shutdown()
                srv.server_close()


if __name__=="__main__":
    main()
