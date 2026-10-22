"""Offline signed owner-bound Hermes subprocess launch admission tests."""
from __future__ import annotations
import time
from unittest.mock import Mock
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from assistx.executor_security import ExecutorTokenCodec
from assistx.hermes_launch_gate import LaunchDenied, verify_owner_bound_launch
from assistx import strict_executor_adapter as ext
from test_strict_executor_adapter import _adapter, _ImmediateProcess

def setup(monkeypatch,tmp_path):
    secret=Ed25519PrivateKey.generate()
    path=tmp_path/"pinned.pub"
    path.write_bytes(secret.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo))
    for name,value in {
        "ASSISTX_EXECUTOR_VERIFY_KEY_FILE":str(path),
        "ASSISTX_REQUIRE_OWNER_BINDING":"true",
        "ASSISTX_STRICT_EXECUTOR_AUTH":"true",
        "HERMES_REQUIRE_OWNER_BOUND_LAUNCH":"true",
        "ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN":"synthetic-bootstrap",
    }.items():monkeypatch.setenv(name,value)
    a=_adapter()
    ext.install_strict_executor_adapter(a)
    c=a.AssistXClient()
    c._active_task_id="t1";c._active_claim_id="c1";c._active_session_id="s1"
    now=int(time.time())
    cl={"iss":"assistx","aud":["assistx-executor"],"iat":now,
        "nbf":now,"exp":now+240,"jti":"j1","task_id":"t1",
        "claim_id":"c1","agent_id":"hermes-test",
        "owner_issuer":"synthetic-idp","owner_subject":"test-owner",
        "projection_generation":2,"scopes":["inference","heartbeat"],
        "allowed_model_aliases":["auto/code"]}
    sign(c,cl,secret)
    return a,c,cl,secret,path

def sign(client,claims,private):
    client.task_token=ExecutorTokenCodec(private_key=private).encode(claims)
    client.task_claims={k:v for k,v in claims.items() if k!="jti"}

def test_good_signed_owner_and_scope(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    assert verify_owner_bound_launch(c,agent_id=a.AGENT_ID,model="auto/code")[
        "owner"]["owner_subject"]=="test-owner"

@pytest.mark.parametrize("field,bad",[
    ("owner_subject","other-owner"),("owner_issuer","other-idp"),
    ("task_id","other-task"),("claim_id","other-claim"),
    ("agent_id","different-agent"),("projection_generation",0),
    ("scopes",["heartbeat"]),("allowed_model_aliases",["other/model"]),
])
def test_changed_signed_claim_fails_closed(monkeypatch,tmp_path,field,bad):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    previous=dict(c.task_claims)
    cl[field]=bad;sign(c,cl,key)
    c.task_claims=previous
    with pytest.raises(LaunchDenied):
        verify_owner_bound_launch(c,agent_id=a.AGENT_ID,model="auto/code")

def test_unsigned_owner_response_change_denied(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    c.task_claims["owner_subject"]="other"
    with pytest.raises(LaunchDenied):
        verify_owner_bound_launch(c,agent_id=a.AGENT_ID,model="auto/code")

def test_wrong_trust_key_denied(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    different=Ed25519PrivateKey.generate()
    path.write_bytes(different.public_key().public_bytes(
        serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    with pytest.raises(LaunchDenied):
        verify_owner_bound_launch(c,agent_id=a.AGENT_ID,model="auto/code")

def test_symlink_key_denied(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    alias=tmp_path/"alias.pub";alias.symlink_to(path)
    monkeypatch.setenv("ASSISTX_EXECUTOR_VERIFY_KEY_FILE",str(alias))
    with pytest.raises(LaunchDenied):
        verify_owner_bound_launch(c,agent_id=a.AGENT_ID,model="auto/code")

def test_expired_signed_token_denied(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    now=int(time.time());cl.update(iat=now-600,nbf=now-600,exp=now-1)
    sign(c,cl,key)
    with pytest.raises(LaunchDenied):
        verify_owner_bound_launch(c,agent_id=a.AGENT_ID,model="auto/code")

def test_missing_owner_required_server_flag_denied(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    monkeypatch.delenv("ASSISTX_REQUIRE_OWNER_BINDING")
    with pytest.raises(LaunchDenied):
        verify_owner_bound_launch(c,agent_id=a.AGENT_ID,model="auto/code")

def test_revoked_lease_denied(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    c._lease_lost.set()
    with pytest.raises(LaunchDenied):
        verify_owner_bound_launch(c,agent_id=a.AGENT_ID,model="auto/code")

def test_child_only_after_new_server_heartbeat(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    events=[]
    def req(method,url,**kw):
        events.append("heartbeat")
        return Mock(raise_for_status=lambda:None,json=lambda:{"ok":True})
    def child(command,**kw):
        events.append("spawn")
        return _ImmediateProcess(command,**kw)
    monkeypatch.setattr(ext.requests,"request",req)
    monkeypatch.setattr(ext.subprocess,"Popen",child)
    assert a.run_hermes("synthetic")["success"] is True
    assert events==["heartbeat","spawn"]

def test_heartbeat_fail_fences_without_child(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    def denied(*args,**kw):raise ext.requests.ConnectionError("offline")
    monkeypatch.setattr(ext.requests,"request",denied)
    monkeypatch.setattr(ext.subprocess,"Popen",
       lambda *args,**kw: (_ for _ in ()).throw(AssertionError("spawned")))
    assert a.run_hermes("synthetic")["error"]=="owner_bound_launch_denied"
    assert c._lease_lost.is_set()

def test_changed_claim_blocks_child(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    c._active_claim_id="changed"
    monkeypatch.setattr(ext.subprocess,"Popen",
       lambda *args,**kw: (_ for _ in ()).throw(AssertionError("spawned")))
    assert a.run_hermes("synthetic")["error"]=="owner_bound_launch_denied"

def test_bad_optin_is_denied(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    monkeypatch.setenv("HERMES_REQUIRE_OWNER_BOUND_LAUNCH","on")
    assert a.run_hermes("synthetic")["error"]=="owner_bound_launch_invalid_mode"

def test_legacy_mode_unchanged(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    monkeypatch.delenv("HERMES_REQUIRE_OWNER_BOUND_LAUNCH")
    monkeypatch.setattr(ext.subprocess,"Popen",
        lambda cmd,**kw:_ImmediateProcess(cmd,**kw))
    assert a.run_hermes("synthetic")["success"] is True
