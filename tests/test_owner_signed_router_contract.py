"""Signed AssistX JWT -> claim status -> optional Auto-Router owner contract.

All principals are synthetic. Neo4j is stubbed, but JWT signatures are real
ephemeral Ed25519 signatures. This is NOT a gateway login or production test.
Set AUTO_ROUTER_OWNER_FENCE_PATH to the local peer source to run the
optional cross-repo verifier tests (no installed Auto-Router dependency).
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

import assistx.executor_security as auth
from assistx.executor_security import ExecutorTokenCodec, ExecutorTokenError, build_executor_security_router
from assistx.executor_claims import assert_claim_matches

OWNER_A={"owner_issuer":"test-idp","owner_subject":"alpha"}
OWNER_B={"owner_issuer":"test-idp","owner_subject":"beta"}


class Row:
    def __init__(self,data):self.data=data
    def single(self):return self.data


class NativeMemoryFake:
    def __init__(self,task):self.task=task
    def _session(self):return self
    def __enter__(self):return self
    def __exit__(self,*_args):return None
    def run(self,query,params):
        now=int(time.time()*1000)
        return Row({
            "task":self.task,
            "projection_generation":81,
            "projection_status":"approved",
            "projection_expires_at_ts":now+60000,
        })
    def close(self):pass


def task(owner:dict[str,str] | None,*,claim="claim-alpha"):
    now=int(time.time()*1000)
    data={
        "id":"task-1","status":"RUNNING",
        "claim_id":claim,"claimed_by":"agent-1",
        "lease_expires_at_ts":now+60000,
    }
    if owner:data.update(owner)
    return data


@pytest.fixture
def signed_api(monkeypatch):
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING","true")
    monkeypatch.setenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN","synthetic-bootstrap-only")
    monkeypatch.setenv("ASSISTX_EXECUTOR_CLAIM_STATUS_TOKEN","synthetic-status-only")
    private=Ed25519PrivateKey.generate()
    signer=ExecutorTokenCodec(private_key=private,key_id="test-ephemeral")
    verifier=ExecutorTokenCodec(public_key=private.public_key(),key_id="test-ephemeral")
    monkeypatch.setattr(auth.ExecutorTokenCodec,"signer_from_env",
                        classmethod(lambda cls:signer))

    def client_for(owner,*,claim="claim-alpha"):
        app=FastAPI()
        app.include_router(build_executor_security_router(
            lambda:NativeMemoryFake(task(owner,claim=claim))
        ))
        return TestClient(app)
    return client_for,verifier


def token_response(client,*,claim="claim-alpha"):
    return client.post("/api/executor/claims/task-1/token",
        headers={"Authorization":"Bearer synthetic-bootstrap-only"},
        json={"agent_id":"agent-1","claim_id":claim})


def status_response(client):
    return client.get("/api/executor/claims/task-1/status",
        headers={"Authorization":"Bearer synthetic-status-only"})


def optional_router_binding():
    path=os.getenv("AUTO_ROUTER_OWNER_FENCE_PATH","")
    if not path:pytest.skip("AUTO_ROUTER_OWNER_FENCE_PATH not set")
    file=Path(path)
    if not file.is_file():pytest.skip("router owner fence source unavailable")
    spec=importlib.util.spec_from_file_location("isolated_router_owner_binding",file)
    assert spec and spec.loader
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_two_synthetic_owners_sign_and_verify_distinct_jwts(signed_api):
    client_for,verifier=signed_api
    alpha=client_for(OWNER_A)
    beta=client_for(OWNER_B,claim="claim-beta")
    a=token_response(alpha);b=token_response(beta,claim="claim-beta")
    assert a.status_code==b.status_code==200
    signed_a=verifier.decode(a.json()["token"],audience="auto-router")
    signed_b=verifier.decode(b.json()["token"],audience="auto-router")
    assert signed_a["owner_subject"]=="alpha"
    assert signed_b["owner_subject"]=="beta"
    assert signed_a["jti"]!=signed_b["jti"]
    assert status_response(alpha).json()["owner_subject"]=="alpha"
    assert status_response(beta).json()["owner_subject"]=="beta"
    assert_claim_matches(signed_a,{
        **status_response(alpha).json(),
        "active":True,
    })
    assert_claim_matches(signed_b,{
        **status_response(beta).json(),
        "active":True,
    })


def test_cross_owner_claim_status_rejected_by_assistx(signed_api):
    client_for,verifier=signed_api
    a=client_for(OWNER_A)
    b=client_for(OWNER_B)
    token=verifier.decode(token_response(a).json()["token"],audience="auto-router")
    status=status_response(b).json()
    assert status["active"]
    with pytest.raises(ExecutorTokenError,match="owner"):
        assert_claim_matches(token,status)


def test_revoked_claim_status_rejected(signed_api):
    client_for,verifier=signed_api
    client=client_for(OWNER_A)
    token=verifier.decode(token_response(client).json()["token"],audience="auto-router")
    revoked=client_for(OWNER_A,claim="claim-revoked")
    status=status_response(revoked).json()
    with pytest.raises(ExecutorTokenError,match="claim generation"):
        assert_claim_matches(token,status)


def test_signature_tampering_rejected(signed_api):
    client_for,verifier=signed_api
    token=token_response(client_for(OWNER_A)).json()["token"]
    a,b,c=token.split(".")
    tampered=a+"."+b+"."+("A" if c[0]!="A" else "B")+c[1:]
    with pytest.raises(ExecutorTokenError,match="signature"):
        verifier.decode(tampered,audience="auto-router")


def test_legacy_ownerless_issuer_rejected(signed_api):
    client_for,_=signed_api
    res=token_response(client_for(None))
    assert res.status_code==409
    assert status_response(client_for(None)).json()["active"] is False


def test_gateway_scope_owner_mismatch_rejected_by_router(signed_api,monkeypatch):
    module=optional_router_binding()
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING","true")
    client_for,verifier=signed_api
    owner_a=verifier.decode(token_response(client_for(OWNER_A)).json()["token"],
                            audience="auto-router")
    scope={"state":{"verified_owner":{**OWNER_B,"authenticated":True}}}
    with pytest.raises(module.OwnerBindingError,match="differs"):
        module.bound_principal(scope,owner_a)


def test_router_rejects_claim_status_owner_switch(signed_api,monkeypatch):
    module=optional_router_binding()
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING","true")
    client_for,verifier=signed_api
    a=verifier.decode(token_response(client_for(OWNER_A)).json()["token"],
                      audience="auto-router")
    b_status=status_response(client_for(OWNER_B)).json()
    with pytest.raises(module.OwnerBindingError,match="owner no longer"):
        module.assert_claim_owner(a,b_status)


def test_router_accepts_only_matched_signed_owner_and_current_status(signed_api,monkeypatch):
    module=optional_router_binding()
    monkeypatch.setenv("AUTO_ROUTER_REQUIRE_OWNER_BINDING","true")
    client_for,verifier=signed_api
    a_client=client_for(OWNER_A)
    a=verifier.decode(token_response(a_client).json()["token"],audience="auto-router")
    verified={"state":{"verified_owner":{**OWNER_A,"authenticated":True}}}
    stamped=module.bound_principal(verified,a)
    assert stamped==OWNER_A
    assert module.assert_claim_owner(stamped,status_response(a_client).json()) is None
