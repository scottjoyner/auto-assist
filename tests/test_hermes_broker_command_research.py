"""Deny arbitrary mutation commands before touching a Neo4j session."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from assistx import hermes_broker_command_research as broker
from assistx.hermes_source_generation_research import GenerationProtocolError

def request(operation="heartbeat"):
    x={"operation":operation,"expected_generation":2,
       "task_id":"synthetic-task","claim_id":"synthetic-claim",
       "session_id":"synthetic-session","attempt":1}
    if operation=="heartbeat":x["ttl_ms"]=15000
    return x

class Scope:
    def __init__(self):
        self.calls=0
    def __call__(self):
        self.calls+=1
        return self
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False

@pytest.mark.parametrize("op",["query","cypher","read","delete","admin",
                                "create","revoke","exec","rollback","raw","Complete",""])
def test_operation_allowlist_denies_unknown_before_database(op):
    s=Scope()
    with pytest.raises(broker.CandidateBrokerCommandError):
        broker.dispatch_candidate_command(
            {"operation":op},trusted_channel_principal="hermes-local",
            session_factory=s)
    assert s.calls==0

@pytest.mark.parametrize("extras",[
    {"cypher":"MATCH(t:Task) SET t.status='DONE'"},
    {"query":"MATCH (t)"},
    {"uri":"bolt://example"},
    {"password":"not-a-secret"},
    {"owner":"alternate"},
    {"principal":"hermes-local"},
    {"generation":2},
    {"ttl_ms":5},
    {"effect":"unverified"},
])
def test_extra_fields_rejected_before_session(extras):
    s=Scope()
    cmd=request("complete")
    cmd.update(extras)
    with pytest.raises(broker.CandidateBrokerCommandError,match="command_fields"):
        broker.dispatch_candidate_command(
            cmd,trusted_channel_principal="hermes-local",session_factory=s)
    assert s.calls==0

@pytest.mark.parametrize("principal",["","legacy-worker","hermes-other",None,True,1])
def test_untrusted_principal_cannot_create_session(principal):
    s=Scope()
    with pytest.raises(broker.CandidateBrokerCommandError,match="unqualified_caller"):
        broker.dispatch_candidate_command(
            request(),trusted_channel_principal=principal,session_factory=s)
    assert s.calls==0

@pytest.mark.parametrize("changes",[
    {"expected_generation":True},{"expected_generation":-1},
    {"attempt":0},{"attempt":True},{"attempt":1.0},
    {"ttl_ms":999},{"ttl_ms":60001},{"ttl_ms":True},
    {"claim_id":""},{"claim_id":"../bad"},{"claim_id":"not allowed"},
    {"task_id":"a"*129},{"session_id":"bad/path"},
])
def test_invalid_type_or_identity_never_allocates_session(changes):
    cmd=request()
    cmd.update(changes)
    s=Scope()
    with pytest.raises(broker.CandidateBrokerCommandError,match="invalid_command_identity"):
        broker.dispatch_candidate_command(cmd,
                trusted_channel_principal="hermes-local",session_factory=s)
    assert s.calls==0

def test_allowed_heartbeat_only_calls_fixed_adapter(monkeypatch):
    s=Scope()
    traces=[]
    def fake(sess,**kw):
        assert sess is s
        traces.append(kw)
        return {"candidate_generation":3}
    monkeypatch.setattr(broker,"renew_candidate_lease",fake)
    out=broker.dispatch_candidate_command(request(),
           trusted_channel_principal="hermes-local",session_factory=s)
    assert s.calls==1
    assert len(traces)==1
    assert traces[0]["claim_id"]=="synthetic-claim"
    assert out["candidate_generation"]==3
    assert out["operation"]=="heartbeat"
    assert out["source_transaction_accepted"]
    for k in ("authenticated_transport_qualified",
              "exclusive_writer_credentials_verified",
              "all_writers_generation_fenced",
              "external_resource_effect_verified",
              "independent_revocation_custody_verified",
              "production_dispatch_authorized"):
        assert out[k] is False
    assert out["decision"]=="HOLD"

def test_allowed_completion_only_calls_fixed_adapter(monkeypatch):
    s=Scope()
    observed=[]
    def fake(sess,**kw):
        observed.append(kw)
        return {"candidate_generation":3}
    monkeypatch.setattr(broker,"complete_candidate_claim",fake)
    out=broker.dispatch_candidate_command(request("complete"),
        trusted_channel_principal="hermes-local",session_factory=s)
    assert s.calls==1
    assert out["operation"]=="complete"
    assert observed[0]["attempt"]==1
    assert "ttl_ms" not in observed[0]
    assert out["decision"]=="HOLD"

def test_failed_source_cas_does_not_leak_task_state(monkeypatch):
    s=Scope()
    def fail(*a,**kw):
        raise GenerationProtocolError("private-task-sensitive-id")
    monkeypatch.setattr(broker,"complete_candidate_claim",fail)
    with pytest.raises(broker.CandidateBrokerCommandError) as info:
        broker.dispatch_candidate_command(request("complete"),
           trusted_channel_principal="hermes-local",session_factory=s)
    assert "private" not in str(info.value)
    assert "unqualified_source_claim" in str(info.value)

def test_no_production_implementation_imports_command_adapter():
    root=Path(__file__).resolve().parents[1]/"src/assistx"
    for f in root.rglob("*.py"):
        if f.name=="hermes_broker_command_research.py":
            continue
        assert "hermes_broker_command_research" not in f.read_text(), f
