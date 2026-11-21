"""Research-only signed gateway owner provenance; all identities synthetic."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request, HTTPException
from fastapi.testclient import TestClient

from assistx.owner_gateway_provenance import (
    AUDIENCE, OwnerGatewayDenied, SignedGatewayOwnerMiddleware,
    install_signed_owner_gateway, verify_attestation,
)
from assistx.owner_identity import TaskOwnerError, authenticated_owner


@pytest.fixture
def gate(monkeypatch,tmp_path):
    issuer=Ed25519PrivateKey.generate()
    public=issuer.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    key=tmp_path/"gateway-owner.pub"
    key.write_bytes(public)
    key.chmod(0o644)  # test runner default umask may create group-writable files
    db=tmp_path/"private-nonce.sqlite"
    monkeypatch.setenv("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY","true")
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING","true")
    monkeypatch.setenv("ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE",str(key))
    monkeypatch.setenv("ASSISTX_OWNER_GATEWAY_NONCE_DB",str(db))
    app=FastAPI()
    @app.post("/api/tasks")
    async def submit(request:Request):
        try:
            owner=authenticated_owner(request.scope,"synthetic-human")
        except TaskOwnerError as exc:
            raise HTTPException(status_code=409,detail="owner mismatch") from exc
        body=await request.body()
        return {"owner":owner,"sha":hashlib.sha256(body).hexdigest()}
    app.add_middleware(SignedGatewayOwnerMiddleware)
    return issuer,TestClient(app),db,key


def signed(issuer,body=b'{"title":"synthetic"}',**changes):
    t=int(time.time())
    data={
        "schema":1,
        "aud":AUDIENCE,
        "owner_issuer":"synthetic-idp",
        "owner_subject":"synthetic-human",
        "method":"POST",
        "path":"/api/tasks",
        "body_sha256":hashlib.sha256(body).hexdigest(),
        "issued_at":t,
        "expires_at":t+30,
        "nonce":secrets.token_hex(32),
    }
    data.update(changes)
    payload=json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
    def b64(raw):
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return b64(payload)+"."+b64(issuer.sign(payload))


def post(client,token,body=b'{"title":"synthetic"}',*,headers=None):
    actual=headers or {"X-AssistX-Signed-Owner":token}
    return client.post("/api/tasks",content=body,headers=actual)


def test_signed_owner_reaches_authenticated_scope(gate):
    issuer,client,db,key=gate
    body=b'{"title":"synthetic"}'
    resp=post(client,signed(issuer,body),body)
    assert resp.status_code==200
    assert resp.json()["owner"]=={
        "owner_issuer":"synthetic-idp","owner_subject":"synthetic-human"}
    assert resp.json()["sha"]==hashlib.sha256(body).hexdigest()
    assert db.stat().st_mode & 0o077==0


def test_replay_is_denied_across_new_http_requests(gate):
    issuer,client,db,key=gate
    token=signed(issuer)
    assert post(client,token).status_code==200
    assert post(client,token).status_code==401


def test_unsigned_header_without_attestation_denied(gate):
    issuer,client,db,key=gate
    assert client.post("/api/tasks",json={"title":"synthetic"}).status_code==401


@pytest.mark.parametrize("change",[
    {"owner_subject":"someone-else"},{"method":"GET"},
    {"path":"/api/other"},{"body_sha256":"0"*64},
    {"aud":"another-audience"},{"schema":2},
    {"issued_at":0,"expires_at":5},
    {"nonce":"bad"},
])
def test_wrong_signed_authority_or_request_denied(gate,change):
    issuer,client,db,key=gate
    expected=409 if "owner_subject" in change else 401
    assert post(client,signed(issuer,**change)).status_code==expected


def test_signed_token_cannot_authorize_different_request_body(gate):
    issuer,client,db,key=gate
    token=signed(issuer)
    assert post(client,token,body=b'{"title":"modified"}').status_code==401


def test_wrong_pinned_gateway_signer_denied(gate):
    issuer,client,db,key=gate
    wrong=Ed25519PrivateKey.generate()
    assert post(client,signed(wrong)).status_code==401


def test_corrupt_signature_and_oversized_header_denied(gate):
    issuer,client,db,key=gate
    raw=signed(issuer)
    assert post(client,raw[:-1]+("A" if raw[-1]!="A" else "B")).status_code==401
    assert post(client,"X"*4096).status_code==401


def test_duplicate_signature_headers_fail_closed(gate):
    issuer,client,db,key=gate
    raw=signed(issuer)
    headers=[("x-assistx-signed-owner",raw),
             ("x-assistx-signed-owner",raw)]
    assert post(client,raw,headers=headers).status_code==401


def test_wrong_trusted_key_file_denied(gate,monkeypatch):
    issuer,client,db,key=gate
    alias=key.parent/"alias.pub"
    alias.symlink_to(key)
    monkeypatch.setenv("ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE",str(alias))
    assert post(client,signed(issuer)).status_code==401


def test_stale_or_future_attestation_denied(gate):
    issuer,client,db,key=gate
    now=int(time.time())
    assert post(client,signed(issuer,issued_at=now-60,expires_at=now-10)).status_code==401
    assert post(client,signed(issuer,issued_at=now+60,expires_at=now+90)).status_code==401


def test_no_signed_gateway_without_explicit_owner_mode(gate,monkeypatch):
    issuer,client,db,key=gate
    monkeypatch.delenv("ASSISTX_REQUIRE_OWNER_BINDING")
    assert post(client,signed(issuer)).status_code==503


def test_no_signed_gateway_without_enable_flag(gate,monkeypatch):
    issuer,client,db,key=gate
    monkeypatch.delenv("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY")
    assert post(client,signed(issuer)).status_code==503


def test_nonce_store_missing_directory_denies_without_creating(gate,monkeypatch):
    issuer,client,db,key=gate
    bad=key.parent/"nonexistent"/"db.sqlite"
    monkeypatch.setenv("ASSISTX_OWNER_GATEWAY_NONCE_DB",str(bad))
    assert post(client,signed(issuer)).status_code==401
    assert not bad.exists()


def test_nonce_ledger_reopens_and_replay_denied(gate):
    issuer,client,db,key=gate
    token=signed(issuer)
    assert post(client,token).status_code==200
    new_app=FastAPI()
    @new_app.post("/api/tasks")
    async def accepted(request:Request):
        return {"owner":authenticated_owner(request.scope,"synthetic-human")}
    new_app.add_middleware(SignedGatewayOwnerMiddleware)
    assert post(TestClient(new_app),token).status_code==401


def test_startup_install_refuses_unprovisioned_trust(gate,monkeypatch):
    issuer,client,db,key=gate
    monkeypatch.setenv("ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE",
                       str(key.parent/"missing.pub"))
    with pytest.raises(OwnerGatewayDenied):
        install_signed_owner_gateway(FastAPI())


def test_standalone_verified_owner_without_basic_principal_is_not_authorized(gate):
    issuer,client,db,key=gate
    # independently signed scope != accepting arbitrary claimed auth_user
    from assistx.owner_identity import TaskOwnerError
    with pytest.raises(TaskOwnerError):
        authenticated_owner({"state":{"verified_owner":{
            "owner_issuer":"synthetic-idp",
            "owner_subject":"synthetic-human","authenticated":True}}},
            "other-user")


def test_whole_nonce_db_rollback_is_not_independent_proof(gate):
    issuer,client,db,key=gate
    first=signed(issuer)
    assert post(client,first).status_code==200
    earlier=db.read_bytes()
    second=signed(issuer)
    assert post(client,second).status_code==200
    db.write_bytes(earlier)
    # Explicit NEGATIVE witness: SQLite-only custody does not resist
    # a privileged restore of both the DB and its consumed nonce history.
    assert post(client,second).status_code==200


def test_default_install_does_not_attach_middleware(monkeypatch):
    monkeypatch.delenv("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY",raising=False)
    app=FastAPI()
    install_signed_owner_gateway(app)
    assert not any(m.cls is SignedGatewayOwnerMiddleware for m in app.user_middleware)


def test_readonly_static_contract():
    source=Path(__file__).parents[1]/"src/assistx/owner_gateway_provenance.py"
    content=source.read_text()
    assert "Ed25519PublicKey" in content
    assert "ASSISTX_REQUIRE_OWNER_BINDING" in content
    assert "signed owner gateway" in content
    assert 'state["verified_owner"]' in content
    assert "authenticated\":True" in content
    assert "method == \"POST\"" in content
