"""Strict signed-gateway mode refuses bare legacy trusted-user headers.

Synthetic ASGI TestClient only. The exact _auth_user_from_credentials
function is compiled from checked-in api.py to avoid importing the live
application's optional LangGraph/runtime dependencies or starting loops.
"""
from __future__ import annotations

import ast
from hmac import compare_digest
import hmac
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException, Request
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient

from assistx.owner_gateway_provenance import SignedGatewayOwnerMiddleware
from assistx.owner_identity import authenticated_owner
from test_owner_gateway_provenance import signed

HEADER="X-Synthetic-Trusted-User"


def real_auth_helper():
    path=Path(__file__).parents[1]/"src/assistx/api.py"
    source=ast.parse(path.read_text())
    function=next(node for node in source.body
                  if isinstance(node,ast.FunctionDef)
                  and node.name=="_auth_user_from_credentials")
    context={
        "__name__":"assistx.isolated_trusted_auth",
        "__package__":"assistx",
        "hmac":hmac,"os":os,
        "Request":Request,
        "HTTPBasicCredentials":HTTPBasicCredentials,
        "Optional":Optional,
        "TRUSTED_AUTH_HEADER":HEADER,
        "USER":"synthetic-admin",
        "PASS":"synthetic-password",
    }
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[function],type_ignores=[])),str(path),"exec"),context)
    return context["_auth_user_from_credentials"]


def request_with_header(*,header="person-a",owner=None,method="POST",
                        path="/api/tasks"):
    verified={"verified_owner":{
        "owner_issuer":"synthetic-idp",
        "owner_subject":owner,
        "authenticated":True,
    }} if owner else {}
    return SimpleNamespace(
        headers=({HEADER:header} if header else {}),
        method=method,url=SimpleNamespace(path=path),
        scope={"state":verified},
    )


@pytest.fixture
def signing(monkeypatch,tmp_path):
    signer=Ed25519PrivateKey.generate()
    key=tmp_path/"gateway.pub"
    key.write_bytes(signer.public_key().public_bytes(
        serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    key.chmod(0o644)
    monkeypatch.setenv("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY","true")
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING","true")
    monkeypatch.setenv("ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE",str(key))
    monkeypatch.setenv("ASSISTX_OWNER_GATEWAY_NONCE_DB",str(tmp_path/"used.sqlite"))
    return signer


def test_bare_header_cannot_authenticate_in_signed_mode(signing):
    assert real_auth_helper()(
        request_with_header(header="person-a"),None) is None


@pytest.mark.parametrize("header,owner,method,path",[
    ("person-b","person-a","POST","/api/tasks"),
    ("person-a","person-b","POST","/api/tasks"),
    ("person-a","person-a","GET","/api/tasks"),
    ("person-a","person-a","POST","/api/tasks/other"),
    ("person-a",None,"POST","/api/tasks"),
])
def test_trusted_header_without_matching_signed_task_owner_fails(
    signing,header,owner,method,path
):
    assert real_auth_helper()(
        request_with_header(header=header,owner=owner,
                            method=method,path=path),None) is None


def test_matching_verified_owner_is_eligible_on_task_post(signing):
    assert real_auth_helper()(
        request_with_header(header="person-a",owner="person-a"),None)=="person-a"


def test_bad_header_cannot_downgrade_to_present_valid_basic_credentials(signing):
    credentials=HTTPBasicCredentials(
        username="synthetic-admin",password="synthetic-password")
    assert real_auth_helper()(
        request_with_header(header="person-b",owner="person-a"),
        credentials) is None


def test_basic_auth_without_untrusted_header_still_works(signing):
    credentials=HTTPBasicCredentials(
        username="synthetic-admin",password="synthetic-password")
    assert real_auth_helper()(
        request_with_header(header=None),credentials)=="synthetic-admin"


def test_legacy_behavior_is_unchanged_without_signed_mode(monkeypatch):
    monkeypatch.delenv("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY",raising=False)
    monkeypatch.delenv("ASSISTX_REQUIRE_OWNER_BINDING",raising=False)
    # This is the source-level risk the live trust-header preflight flags:
    # if an untrusted client can reach this auth path, a raw header suffices.
    assert real_auth_helper()(
        request_with_header(header="person-a"),None)=="person-a"


def test_actual_signed_middleware_and_auth_helper_agree_on_owner(signing):
    auth=real_auth_helper()
    app=FastAPI()

    @app.post("/api/tasks")
    async def isolated_submit(request:Request):
        user=auth(request,None)
        if not user:
            raise HTTPException(status_code=401)
        owner=authenticated_owner(request.scope,user)
        return owner

    @app.get("/api/tasks")
    async def isolated_list(request:Request):
        if not auth(request,None):
            raise HTTPException(status_code=401)
        return {"unsafe":"should-not-appear"}

    app.add_middleware(SignedGatewayOwnerMiddleware)
    client=TestClient(app)
    body=b'{"title":"synthetic"}'
    sig=signed(signing,body,owner_subject="person-a")
    good=client.post("/api/tasks",content=body,headers={
        HEADER:"person-a","X-AssistX-Signed-Owner":sig,
        "Content-Type":"application/json"})
    assert good.status_code==200
    assert good.json()["owner_subject"]=="person-a"

    wrong=client.post("/api/tasks",content=body,headers={
        HEADER:"person-b","X-AssistX-Signed-Owner":
            signed(signing,body,owner_subject="person-a"),
        "Content-Type":"application/json"})
    assert wrong.status_code==401

    forged=client.post("/api/tasks",content=body,headers={
        HEADER:"person-a","X-AssistX-Signed-Owner":"forged"})
    assert forged.status_code==401

    raw=client.get("/api/tasks",headers={HEADER:"person-a"})
    assert raw.status_code==401
