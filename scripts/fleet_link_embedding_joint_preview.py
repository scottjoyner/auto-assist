#!/usr/bin/env python3
"""Synthetic cross-repo read-only admission preview on real Fleet Link handlers.

No live servers, device connections, inference calls, disk scans or prod keys.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx import runtime_projection as legacy
from assistx import runtime_projection_v2 as signing
from assistx.fleet_link_embedding_preview import build_fleet_link_embedding_preview_router


class FakeEmbeddingBackend(BaseHTTPRequestHandler):
    posts=0

    def log_message(self,*args): return

    def do_POST(self):
        type(self).posts+=1
        self.send_error(405)

    def do_GET(self):
        if self.path=="/health":doc={"status":"ok"}
        elif self.path=="/props":doc={"model_alias":"fixture-embed"}
        else:return self.send_error(404)
        body=json.dumps(doc).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start(handler):
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fleet-link-root",required=True,type=Path)
    args=p.parse_args()
    root=args.fleet_link_root.resolve()
    if not (root/"services"/"fleet_model_embedding.py").is_file():
        raise SystemExit("Fleet Link embedding PR source required")
    sys.path.insert(0,str(root/"services"))
    from fleet_model_link import Link,SCHEMA,canonical,handler

    backend=sidecar=None
    try:
        backend=start(FakeEmbeddingBackend)
        fleet_signer=Ed25519PrivateKey.generate()
        assistx_signer=Ed25519PrivateKey.generate()
        pub=fleet_signer.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        assert fleet_signer.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw) != assistx_signer.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        manifest={
            "schema":SCHEMA,"epoch":"synthetic","generation":5,
            "expires_at":int(time.time()+60),
            "nodes":[{"id":"lenovo","peer_url":None}],
            "instances":[{
                "node":"lenovo","alias":"embed","upstream_id":"fixture-embed",
                "port":backend.server_port,"kind":"embedding",
                "power_policy":"BATTERY_CANDIDATE",
            }],
        }
        envelope={"manifest":manifest,"signature_hex":fleet_signer.sign(canonical(manifest)).hex()}
        link=Link(node_id="lenovo",manifest=envelope,pinned_pubkey_hex=pub)
        sidecar=start(handler(link))
        url=f"http://127.0.0.1:{sidecar.server_port}"
        now=int(time.time()*1000)
        projection={
            "source":"assistx","schema_version":"2","generation":8,
            "revision":"synthetic-only",
            "generated_at_ms":now,"expires_at_ms":now+30000,
            "signature_algorithm":"Ed25519","signature_key_id":"synthetic",
            "providers":[{
                "name":"synthetic-approved-loopback",
                "enabled":True,"node_id":"lenovo",
                "runtime_instance_id":"fleetlink:lenovo",
                "runtime_kind":"openai_compatible",
                "type":"openai_compatible",
                "base_url":url+"/v1","access_urls":[url+"/v1"],
                "models":[{
                    "provider_model":"lenovo/embed","alias":"Synthetic Embed",
                    "artifact_fingerprint":"sha256:"+"f"*64,
                    "capabilities":["embedding","local_only"],
                }],
            }],
        }
        projection["checksum"]=legacy.projection_checksum(projection)
        projection["signature"]=signing.projection_signature(projection,assistx_signer)
        signing_message=signing.signing_message(projection)
        assistx_signer.public_key().verify(
            __import__("base64").urlsafe_b64decode(projection["signature"]+"=="),
            signing_message,
        )
        def auth():return "synthetic-authenticated-operator"
        app=FastAPI()
        app.include_router(build_fleet_link_embedding_preview_router(
            auth,lambda: projection))
        with patch.dict(__import__("os").environ,{
            "ASSISTX_FLEET_LINK_EMBEDDING_PREVIEW":"1",
            "ASSISTX_FLEET_LINK_CANARY":"0",
            "ASSISTX_FLEET_LINK_URL":url,
            "ASSISTX_FLEET_LINK_RUNTIME_ID":"fleetlink:lenovo",
        }):
            reply=TestClient(app).get("/api/fleet-link/embeddings/preview")
        assert reply.status_code==200,reply.text
        data=reply.json()
        assert data["matched_embedding_count"]==1,data
        assert data["admitted_embedding_count"]==1
        assert data["observed_ready_embedding_count"]==1
        assert not data["execution_authorized"]
        assert not data["artifact_identity_independently_proved"]
        assert FakeEmbeddingBackend.posts==0
        print(json.dumps({
            "schema":"assistx-fleet-link-embedding-joint-acceptance.v1",
            "result":"PASS","scope":"SYNTHETIC_LOCALHOST_ONLY",
            "actual_fleet_link_handler":True,
            "signed_catalog_test_key_ephemeral":True,
            "signed_assistx_projection_test_key_ephemeral":True,
            "two_independent_ephemeral_signers":True,
            "admitted_count":data["admitted_embedding_count"],
            "reported_ready_count":data["observed_ready_embedding_count"],
            "intersection_count":data["matched_embedding_count"],
            "inference_post_requests":FakeEmbeddingBackend.posts,
            "production_deployment":False,
        },sort_keys=True))
    finally:
        for server in (sidecar,backend):
            if server:
                server.shutdown()
                server.server_close()


if __name__=="__main__":
    main()
