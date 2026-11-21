"""Two-owner synthetic HTTP integration through the real AssistX task handler.

Runs FastAPI's actual api_create_task and Neo4jClient.create_owned_task against
a disposable in-memory Neo4j session double. No live DB/task/API mutation.
Authentication here is ONLY a synthetic two-user stub. It cannot qualify an
independent production identity provider, or claim actual gateway deployment.
"""
from __future__ import annotations

import ast
import base64
from concurrent.futures import ThreadPoolExecutor
from hmac import compare_digest
import json
from pathlib import Path
import secrets
import sys
import types
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field
from fastapi import Depends

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

import assistx.neo4j_client as neo_module
from assistx.neo4j_client import Neo4jClient
from assistx.owner_gateway_provenance import SignedGatewayOwnerMiddleware
from test_owner_authority_contract import MemoryStore
from test_owner_gateway_provenance import signed
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

IDENTITIES={"person-a":"synthetic-password-a","person-b":"synthetic-password-b"}


def exact_task_handler(auth, neo_factory):
    """Compile EXACT checked-in route and Pydantic request class in isolation.

    The full application imports LangGraph and starts background integrations
    not installed/authorized in this research runner; never invoke those.
    """
    source=Path(__file__).parents[1]/"src/assistx/api.py"
    module=ast.parse(source.read_text())
    schema=next(x for x in module.body
                if isinstance(x,ast.ClassDef) and x.name=="TaskCreateIn")
    handler=next(x for x in module.body
                 if isinstance(x,ast.FunctionDef) and x.name=="api_create_task")
    namespace={
        "__name__":"assistx.synthetic_exact_task_handler",
        "__package__":"assistx",
        "Any":Any,"Dict":dict,"List":list,"Optional":Optional,
        "BaseModel":BaseModel,"ConfigDict":ConfigDict,"Field":Field,
        "Depends":Depends,"Request":Request,"HTTPException":HTTPException,
        "auth":auth,"_neo":neo_factory,
    }
    # FastAPI's @app.post decorator belongs to the global production app;
    # register this exact handler on our disposable FastAPI app instead.
    handler.decorator_list=[]
    fragment=ast.fix_missing_locations(ast.Module(body=[schema,handler],type_ignores=[]))
    isolated=types.ModuleType(namespace["__name__"])
    isolated.__dict__.update(namespace)
    sys.modules[isolated.__name__]=isolated
    exec(compile(fragment,str(source),"exec"),isolated.__dict__)
    return isolated.api_create_task


@pytest.fixture
def synthetic_signer(monkeypatch,tmp_path):
    signer=Ed25519PrivateKey.generate()
    key=tmp_path/"synthetic-gateway.pub"
    key.write_bytes(signer.public_key().public_bytes(
        serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    key.chmod(0o644)
    nonce_db=tmp_path/"synthetic-nonces.sqlite"
    monkeypatch.setenv("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY","true")
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING","true")
    monkeypatch.setenv("ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE",str(key))
    monkeypatch.setenv("ASSISTX_OWNER_GATEWAY_NONCE_DB",str(nonce_db))
    return signer,nonce_db,key


@pytest.fixture
def task_route(synthetic_signer,monkeypatch):
    signer,nonce_db,key=synthetic_signer
    store=MemoryStore()
    neo=Neo4jClient.__new__(Neo4jClient)
    neo._session=store.session

    class DisposableNeo:
        def create_owned_task(self,**kwargs):
            return neo.create_owned_task(**kwargs)
        def close(self):
            pass

    created=[]
    monkeypatch.setattr(
        neo_module,"notify_task_created",lambda **kw:created.append(kw))
    app=FastAPI()

    def synthetic_basic_user(request:Request):
        raw=request.headers.get("Authorization","")
        if not raw.startswith("Basic "):
            raise HTTPException(status_code=401,detail="synthetic login required")
        try:
            decoded=base64.b64decode(raw[6:],validate=True).decode("utf-8")
        except (ValueError,UnicodeError):
            raise HTTPException(status_code=401,detail="synthetic login required") from None
        for person,pw in IDENTITIES.items():
            if compare_digest(decoded,f"{person}:{pw}"):
                return person
        raise HTTPException(status_code=401,detail="synthetic login required")

    app.add_api_route("/api/tasks",exact_task_handler(
        synthetic_basic_user,lambda:DisposableNeo()),methods=["POST"])
    app.add_middleware(SignedGatewayOwnerMiddleware)
    return signer,TestClient(app),store,created


def send(signer,client,*,subject="person-a",login=None,
         idempotency="shared-id",token=None,body=None,headers=None):
    if body is None:
        body=json.dumps({
            "title":"Synthetic owned request", "task_type":"swarm_task",
            "idempotency_key":idempotency,
            "payload":{"source":"isolated-research-only"}},
            sort_keys=True,separators=(",",":")).encode()
    if token is None:
        token=signed(signer,body,owner_subject=subject)
    identity=subject if login is None else login
    auth="Basic "+base64.b64encode(
        (identity+":"+IDENTITIES[identity]).encode()).decode()
    return client.post("/api/tasks",content=body,headers={
        "Authorization":auth,"X-AssistX-Signed-Owner":token,
        "Content-Type":"application/json",
        **(headers or {}),
    })


def test_two_humans_get_distinct_owned_tasks_even_with_shared_idempotency(task_route):
    signer,client,store,created=task_route
    alpha=send(signer,client,subject="person-a",idempotency="same")
    beta=send(signer,client,subject="person-b",idempotency="same")
    assert alpha.status_code==200,alpha.text
    assert beta.status_code==200,beta.text
    assert alpha.json()["task_id"]!=beta.json()["task_id"]
    assert store.writes==2
    assert len(created)==2
    retry=send(signer,client,subject="person-a",idempotency="same")
    assert retry.status_code==200
    assert retry.json()["task_id"]==alpha.json()["task_id"]
    assert store.writes==2


def test_cross_user_signed_attestation_cannot_impersonate_login(task_route):
    signer,client,store,created=task_route
    response=send(signer,client,subject="person-a",login="person-b")
    assert response.status_code==409
    assert store.writes==0 and created==[]


def test_no_signed_scope_from_untrusted_owner_headers(task_route):
    signer,client,store,created=task_route
    result=send(signer,client,subject="person-a",token="forged",headers={
        "X-Owner-Subject":"person-a",
        "X-Verified-Owner":"person-a",
        "X-AssistX-Authenticated-Owner":"person-a",
    })
    assert result.status_code==401
    assert store.writes==0 and created==[]


def test_attestation_for_other_body_cannot_create_owned_task(task_route):
    signer,client,store,created=task_route
    earlier=b'{"title":"DIFFERENT REQUEST"}'
    result=send(signer,client,subject="person-a",
                token=signed(signer,earlier,owner_subject="person-a"))
    assert result.status_code==401
    assert store.writes==0


def test_reused_signed_assertion_rejected_before_duplicate_task(task_route):
    signer,client,store,created=task_route
    body=b'{"title":"Synthetic request","idempotency_key":"retry"}'
    token=signed(signer,body,owner_subject="person-a")
    first=send(signer,client,body=body,token=token)
    replay=send(signer,client,body=body,token=token)
    assert first.status_code==200 and replay.status_code==401
    assert store.writes==1 and len(created)==1


def test_claim_with_wrong_basic_password_denied_even_if_signed(task_route):
    signer,client,store,created=task_route
    token=signed(signer,b'{"title":"test"}',owner_subject="person-a")
    response=send(signer,client,token=token,headers={
        "Authorization":"Basic "+base64.b64encode(
            b"person-a:wrong-password").decode()})
    assert response.status_code==401
    assert store.writes==0


def test_another_owner_cannot_modify_first_owner_using_same_idempotency(task_route):
    signer,client,store,created=task_route
    first=send(signer,client,subject="person-a",idempotency="immutable")
    before=list(store.rows.values())[0].copy()
    second=send(signer,client,subject="person-b",idempotency="immutable")
    assert first.status_code==second.status_code==200
    assert first.json()["task_id"]!=second.json()["task_id"]
    assert list(store.rows.values())[0]==before
    assert store.writes==2


def test_concurrent_replay_wins_once_and_one_unauthorized(task_route):
    signer,client,store,created=task_route
    body=b'{"title":"Synthetic race","idempotency_key":"race"}'
    token=signed(signer,body,owner_subject="person-a")
    def work(_):
        return send(signer,client,body=body,token=token).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes=list(pool.map(work,range(2)))
    assert sorted(codes)==[200,401]
    assert store.writes==1 and len(created)==1
