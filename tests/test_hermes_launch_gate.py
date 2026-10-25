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

def heartbeat_ack():
    return {"task":{
        "id":"t1","claim_id":"c1","claimed_by":"hermes-test",
        "agent_session_id":"s1","status":"RUNNING",
        "owner_issuer":"synthetic-idp","owner_subject":"test-owner",
        "lease_expires_at_ts":int(time.time()*1000)+600_000,
    }}

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
        return Mock(raise_for_status=lambda:None,json=heartbeat_ack)
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


def test_revocation_during_popen_handoff_denies_instant_exit(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(ext.requests,"request",lambda *_a,**_kw:
                        Mock(raise_for_status=lambda:None,json=heartbeat_ack))
    launched=[]
    def race(command,**kwargs):
        launched.append(True)
        c._lease_lost.set()  # revoke before _active_process is registered
        return _ImmediateProcess(command,**kwargs)
    monkeypatch.setattr(ext.subprocess,"Popen",race)
    result=a.run_hermes("revoke-during-spawn")
    assert launched==[True]
    assert result["success"] is False
    assert result["error"]=="executor_lease_lost"
    assert result["output"]==""
    assert result["session_id"] is None
    assert c._active_process is None


def test_revocation_after_process_exit_before_stdout_rejected(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(ext.requests,"request",lambda *_a,**_kw:
                        Mock(raise_for_status=lambda:None,json=heartbeat_ack))
    class FinishesAndRevokes(_ImmediateProcess):
        def communicate(self):
            c._lease_lost.set()  # a completed process is not a valid receipt
            return "Session ID: secret-inference\nprivate-result", ""
    monkeypatch.setattr(ext.subprocess,"Popen",
                        lambda cmd,**kw: FinishesAndRevokes(cmd,**kw))
    result=a.run_hermes("revoke-during-output")
    assert result["success"] is False
    assert result["error"]=="executor_lease_lost"
    assert result["output"]==""
    assert result["session_id"] is None


def test_revocation_while_process_is_running_is_fenced(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(ext.requests,"request",lambda *_a,**_kw:
                        Mock(raise_for_status=lambda:None,json=heartbeat_ack))
    events=[]
    class Running:
        pid=9999999
        returncode=None
        def __init__(self,command,**kwargs): events.append("spawn")
        def poll(self): return self.returncode
        def communicate(self): return "Session ID: unauthorized\nprivate", ""
    def terminate(p,grace_seconds=3):
        events.append("terminated")
        p.returncode=-15
    def lose_lease(timeout):
        events.append("revoked")
        c._lease_lost.set()
        return True
    monkeypatch.setattr(ext.subprocess,"Popen",Running)
    monkeypatch.setattr(ext,"_terminate_process",terminate)
    monkeypatch.setattr(c._lease_lost,"wait",lose_lease)
    result=a.run_hermes("running-revocation")
    assert events==["spawn","revoked","terminated"]
    assert result["error"]=="executor_lease_lost"
    assert result["output"]==""
    assert result["session_id"] is None
    assert c._active_process is None


def test_no_authoritative_completion_after_revocation(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    c._lease_lost.set()
    monkeypatch.setattr(ext.requests,"request",lambda *_a,**_kw:
        (_ for _ in ()).throw(AssertionError("completion after lease revocation")))
    c.complete_task("t1","s1","COMPLETED",summary="must-not-publish",claim_id="c1")
    assert c.task_token==""


@pytest.mark.parametrize("field,value", [
    ("id","other-task"),
    ("claim_id","old-claim"),
    ("claimed_by","other-agent"),
    ("agent_session_id","another-session"),
    ("status","PAUSING"),
    ("status","CANCELLED"),
    ("owner_issuer","untrusted-idp"),
    ("owner_subject","different-human"),
    ("lease_expires_at_ts",0),
    ("lease_expires_at_ts",False),
])
def test_heartbeat_http_200_with_conflicting_task_denied(
    monkeypatch,tmp_path,field,value
):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    task=heartbeat_ack()
    task["task"][field]=value
    monkeypatch.setattr(ext.requests,"request",lambda *_a,**_kw:
                        Mock(raise_for_status=lambda:None,json=lambda:task))
    with pytest.raises(LaunchDenied):
        c.heartbeat("t1","s1","c1")


@pytest.mark.parametrize("body",[
    {"ok":True}, {"task":None}, {"task":{}}, [],
    {"task":{"status":"RUNNING"}}, {"error":"revoked"},
])
def test_success_http_without_authoritative_claim_echo_denied(
    monkeypatch,tmp_path,body
):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(ext.requests,"request",lambda *_a,**_kw:
                        Mock(raise_for_status=lambda:None,json=lambda:body))
    with pytest.raises(LaunchDenied):
        c.heartbeat("t1","s1","c1")


def test_heartbeat_ack_from_real_api_contract_passes(monkeypatch,tmp_path):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(ext.requests,"request",lambda *_a,**_kw:
                        Mock(raise_for_status=lambda:None,json=heartbeat_ack))
    c.heartbeat("t1","s1","c1")
    assert c._lease_lost.is_set() is False


def test_bad_initial_claim_heartbeat_immediately_fences_before_dispatch(
    monkeypatch,tmp_path
):
    a,c,claims,key,path=setup(monkeypatch,tmp_path)
    bad=heartbeat_ack()
    bad["task"]["owner_subject"]="revoked-owner"
    calls=[]
    def request(method,url,**kw):
        calls.append(url)
        if url.endswith("/claim"):
            reply={"claimed":True,"task":{"claim_id":"c1"}}
        elif url.endswith("/token"):
            reply={"token":c.task_token,"claims":c.task_claims}
        else:
            reply=bad
        return Mock(raise_for_status=lambda:None,json=lambda:reply)
    monkeypatch.setattr(ext.requests,"request",request)
    with pytest.raises(LaunchDenied):
        c.claim_task("t1","s1")
    assert len(calls)==3
    assert c._lease_lost.is_set()
    assert c._heartbeat_thread is None


def test_shorter_heartbeat_lease_than_signed_token_is_denied(
    monkeypatch,tmp_path
):
    a,c,cl,key,path=setup(monkeypatch,tmp_path)
    task=heartbeat_ack()
    task["task"]["lease_expires_at_ts"]=int(time.time()*1000)+1000
    monkeypatch.setattr(ext.requests,"request",lambda *_a,**_kw:
                        Mock(raise_for_status=lambda:None,json=lambda:task))
    with pytest.raises(LaunchDenied):
        c.heartbeat("t1","s1","c1")
