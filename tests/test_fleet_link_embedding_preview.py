"""Operator-only embedding preview stays separate from chat and model execution."""
from __future__ import annotations

import copy
import json
import sys
import time
import types

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx import runtime_projection as legacy
from assistx import runtime_projection_v2
from assistx.fleet_link_embedding_preview import (
    _admitted_embedding_routes,
    _observed_embedding_routes,
    build_fleet_link_embedding_preview_router,
)
from assistx.runtime_projection_v2 import signing_message


URL="http://127.0.0.1:19341"
MODEL="lenovo/embed"
ARTIFACT="sha256:" + "a"*64


def approved_projection(*,route=MODEL,capabilities=None):
    now=int(time.time()*1000)
    doc={
        "schema_version":"2","source":"assistx",
        "generation":3,"revision":"synthetic-3",
        "generated_at_ms":now,"expires_at_ms":now+60000,
        "signature_algorithm":"Ed25519",
        "signature_key_id":"test-issuer",
        "providers":[{
            "name":"synthetic-approved-runtime","enabled":True,
            "node_id":"lenovo","runtime_instance_id":"fleetlink:lenovo",
            "runtime_kind":"openai_compatible","type":"openai_compatible",
            "base_url":URL+"/v1","access_urls":[URL+"/v1"],
            "models":[{
                "alias":"Nomic Embed","provider_model":route,
                "artifact_fingerprint":ARTIFACT,
                "capabilities":capabilities or ["embedding","local_only"],
            }],
        }],
    }
    doc["checksum"]=legacy.projection_checksum(doc)
    key=Ed25519PrivateKey.generate()
    doc["signature"]=runtime_projection_v2.projection_signature(doc,key)
    key.public_key().verify(
        __import__("base64").urlsafe_b64decode(doc["signature"]+"=="),
        signing_message(doc),
    )
    return doc


def sidecar(*,kind="embedding",ready=True,expired=False):
    return (
        {
            "schema":"fleet-model-link.v1","node":"lenovo","generation":1,
            "catalog_expired":expired,
            "models":[{
                "id":MODEL,"kind":kind,"status":"READY_PROVED_RECENTLY" if ready else "NOT_VERIFIED",
                "ready_now":ready,
            }],
        },
        {"object":"list","data":[
            {"id":MODEL,"kind":kind,"object":"model"}
        ]},
    )


def install(monkeypatch,*, projection=None,status=None,inventory=None,
            auth=True,raise_network=False):
    monkeypatch.setenv("ASSISTX_FLEET_LINK_EMBEDDING_PREVIEW","1")
    monkeypatch.setenv("ASSISTX_FLEET_LINK_URL",URL)
    monkeypatch.setenv("ASSISTX_FLEET_LINK_RUNTIME_ID","fleetlink:lenovo")
    projection=projection if projection is not None else approved_projection()
    default_status,default_inventory=sidecar()
    status=status if status is not None else default_status
    inventory=inventory if inventory is not None else default_inventory
    captured=[]
    def exchange(request):
        captured.append((request.method,request.url.path))
        if raise_network:
            raise httpx.ConnectError("synthetic sidecar unavailable")
        if request.url.path=="/fleet/nodes":
            return httpx.Response(200,json=status)
        if request.url.path=="/fleet/embeddings":
            return httpx.Response(200,json=inventory)
        return httpx.Response(599,content=b"never")
    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(exchange),
            follow_redirects=False,trust_env=False)
    def auth_dep():
        if not auth:
            raise HTTPException(401,detail="not authorized")
        return "operator"
    api=FastAPI()
    api.include_router(build_fleet_link_embedding_preview_router(
        auth_dep,lambda:projection,factory))
    return TestClient(api),captured


def test_only_operator_can_observe_embedded_proof_intersection(monkeypatch):
    client,calls=install(monkeypatch)
    result=client.get("/api/fleet-link/embeddings/preview")
    assert result.status_code==200
    data=result.json()
    assert data=={
        "schema":"assistx-fleet-link-embedding-preview.v1",
        "mode":"OPERATOR_OBSERVATION_ONLY",
        "admitted_embedding_count":1,
        "observed_ready_embedding_count":1,
        "matched_embedding_count":1,
        "execution_authorized":False,
        "automatic_promotion":False,
        "artifact_identity_independently_proved":False,
        "sidecar_identity_cryptographically_verified":False,
        "projection_ed25519_pin_verified":False,
        "catalog_generation_hint":1,
    }
    assert calls==[("GET","/fleet/nodes"),("GET","/fleet/embeddings")]
    serialized=json.dumps(data)
    for secret in (ARTIFACT,MODEL,"lenovo","127.0.0.1","fleetlink","signature"):
        assert secret not in serialized


def test_auth_rejected_before_any_sidecar_probe(monkeypatch):
    client,calls=install(monkeypatch,auth=False)
    assert client.get("/api/fleet-link/embeddings/preview").status_code==401
    assert calls==[]


def test_embedding_feature_off_stays_off_even_if_chat_canary_enabled(monkeypatch):
    client,calls=install(monkeypatch)
    monkeypatch.setenv("ASSISTX_FLEET_LINK_EMBEDDING_PREVIEW","0")
    monkeypatch.setenv("ASSISTX_FLEET_LINK_CANARY","1")
    data=client.get("/api/fleet-link/embeddings/preview").json()
    assert data["mode"]=="NOT_VERIFIED"
    assert calls==[]


def test_readonly_preview_does_not_enable_chat_canary(monkeypatch):
    client,calls=install(monkeypatch)
    monkeypatch.setenv("ASSISTX_FLEET_LINK_CANARY","0")
    assert client.get("/api/fleet-link/embeddings/preview").json()["matched_embedding_count"]==1
    assert all(method=="GET" for method,path in calls)


@pytest.mark.parametrize("bad",[
    "http://localhost:19341",
    "http://127.0.0.2:19341",
    "http://127.0.0.1:19341/extra",
    "https://127.0.0.1:19341",
    "http://name:pass@127.0.0.1:19341",
    "http://127.0.0.1:19341?mode=write",
    "http://0.0.0.0:19341",
])
def test_nonliteral_sidecar_is_never_contacted(monkeypatch,bad):
    client,calls=install(monkeypatch)
    monkeypatch.setenv("ASSISTX_FLEET_LINK_URL",bad)
    assert client.get("/api/fleet-link/embeddings/preview").json()["matched_embedding_count"]==0
    assert calls==[]


@pytest.mark.parametrize("mutate",[
    lambda d:d.update(expires_at_ms=1),
    lambda d:d.update(signature="none"),
    lambda d:d.update(signature_algorithm="HMAC"),
    lambda d:d.update(schema_version="1"),
    lambda d:d.update(generated_at_ms=1),
    lambda d:d.update(generation=0),
    lambda d:d.update(checksum="tampered"),
    lambda d:d["providers"][0]["models"][0].update(artifact_fingerprint="unknown"),
    lambda d:d["providers"][0]["models"][0].update(artifact_fingerprint="sha256:1234"),
    lambda d:d["providers"][0]["models"][0].update(capabilities=["chat","local_only"]),
    lambda d:d["providers"][0]["models"][0].update(capabilities=["chat","embedding","local_only"]),
    lambda d:d["providers"][0]["models"][0].update(capabilities=["embedding"]),
    lambda d:d["providers"][0]["models"][0].update(provider_model="auto/local"),
    lambda d:d["providers"][0].update(runtime_instance_id="unapproved"),
    lambda d:d["providers"][0].update(base_url="http://elsewhere/v1"),
    lambda d:d["providers"][0].update(access_urls=["http://127.0.0.1:19341/v1","http://another/v1"]),
    lambda d:d["providers"][0].update(enabled=False),
    lambda d:d["providers"][0]["models"][0].update(allow_code_execution=True),
    lambda d:d["providers"][0]["models"][0].update(allow_agent_runtime=True),
])
def test_incomplete_authority_blocks_without_network(monkeypatch,mutate):
    doc=approved_projection()
    mutate(doc)
    client,calls=install(monkeypatch,projection=doc)
    result=client.get("/api/fleet-link/embeddings/preview")
    assert result.status_code==200
    assert result.json()["matched_embedding_count"]==0
    assert calls==[]


def test_duplicate_route_quarantined(monkeypatch):
    doc=approved_projection()
    doc["providers"][0]["models"].append(copy.deepcopy(doc["providers"][0]["models"][0]))
    client,calls=install(monkeypatch,projection=doc)
    assert client.get("/api/fleet-link/embeddings/preview").json()["matched_embedding_count"]==0
    assert calls==[]


@pytest.mark.parametrize("mutate",[
    lambda s,i:s.update(catalog_expired=True),
    lambda s,i:s.update(generation=0),
    lambda s,i:s["models"][0].update(ready_now=False),
    lambda s,i:s["models"][0].update(kind="chat"),
    lambda s,i:s["models"][0].update(status="NOT_VERIFIED"),
    lambda s,i:i["data"][0].update(kind="chat"),
    lambda s,i:i["data"][0].update(id="other/model"),
    lambda s,i:i.update(object="wrong"),
    lambda s,i:s["models"].append(copy.deepcopy(s["models"][0])),
    lambda s,i:i["data"].append(copy.deepcopy(i["data"][0])),
    lambda s,i:s.update(schema="invented"),
])
def test_sidecar_both_views_required_for_observed_match(monkeypatch,mutate):
    s,i=sidecar()
    mutate(s,i)
    client,calls=install(monkeypatch,status=s,inventory=i)
    result=client.get("/api/fleet-link/embeddings/preview")
    assert result.status_code==200
    assert result.json()["matched_embedding_count"]==0
    assert calls==[("GET","/fleet/nodes"),("GET","/fleet/embeddings")]


def test_no_approved_embedding_cannot_count_ready_chat_alias(monkeypatch):
    doc=approved_projection(route="lenovo/other-embedding")
    client,calls=install(monkeypatch,projection=doc)
    result=client.get("/api/fleet-link/embeddings/preview")
    assert result.json()["admitted_embedding_count"]==1
    assert result.json()["observed_ready_embedding_count"]==1
    assert result.json()["matched_embedding_count"]==0


def test_observation_fails_closed_when_sidecar_network_unavailable(monkeypatch):
    client,calls=install(monkeypatch,raise_network=True)
    result=client.get("/api/fleet-link/embeddings/preview")
    assert result.status_code==200
    assert result.json()["mode"]=="NOT_VERIFIED"
    assert result.json()["admitted_embedding_count"]==0
    assert calls==[("GET","/fleet/nodes")]


def test_only_get_registered_not_embedding_post_or_runtime_promotion(monkeypatch):
    client,calls=install(monkeypatch)
    assert client.post("/api/fleet-link/embeddings/preview").status_code==405
    assert all(method=="GET" for method,path in calls)


def test_versioned_projection_import_is_compatible_with_current_issuer(monkeypatch):
    from assistx import runtime_projection_v2 as signing
    from assistx import mobile_agent_routes as mobile
    assert signing.build_runtime_projection_v2 is signing.build_runtime_projection
    monkeypatch.setattr(signing,"build_runtime_projection_v2",lambda neo,ttl_seconds:approved_projection())
    monkeypatch.setitem(sys.modules,"assistx.api",types.SimpleNamespace(_neo=lambda: None))
    assert mobile._current_runtime_projection()["signature_algorithm"]=="Ed25519"
