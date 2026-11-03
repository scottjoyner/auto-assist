"""UHP 2026-10-19 adapter safety guards, synthetic transport only.

No model generation, live server, SSH, network, CI account, or filesystem writes.
The UHP conformance suite must still be run independently before claiming
any implementation class; these are narrow adapter behavioral tests.
"""
from __future__ import annotations

from io import BytesIO
import json
from urllib import error
from unittest.mock import patch
import pytest

from assistx.harnessrouter_adapter import HarnessRouterAdapter, HarnessRouterConfig


class Stub:
    def __init__(self, overrides=None):
        self.calls=[]
        self.overrides=overrides or {}

    def __call__(self,method,path,body):
        self.calls.append((method,path,body))
        if (method,path) in self.overrides:
            x=self.overrides[(method,path)]
            if isinstance(x,Exception):
                raise x
            return x
        if path=="/v1/responses":
            return {"id":"resp_a1","status":"in_progress","model":body["model"],
                    "metadata":{"session_id":"sess1"},"usage":{"input_tokens":10,"output_tokens":0}}
        if path=="/v1/responses/resp_a1":
            return {"id":"resp_a1","status":"completed","model":"fixture-model","metadata":{"session_id":"sess1"}}
        if path=="/v1/responses/resp_a1/cancel":
            return {"id":"resp_a1","status":"cancelled","metadata":{"session_id":"sess1"}}
        raise AssertionError(path)


def adapter(transport=None, *, allow=("h-1",), enabled=True):
    transport=transport or Stub()
    return HarnessRouterAdapter(
        HarnessRouterConfig(base_url="http://localhost:3000",
                            allowed_harness_ids=allow,canary_enabled=enabled),
        request_fn=transport), transport


def submit(a,harness="h-1",model="fixture-model"):
    return a.submit_canary("synthetic-only",harness_id=harness,model=model,explicit_opt_in=True)


def test_adapter_starts_with_zero_owned_responses():
    a,t=adapter()
    assert a._owned_responses=={}
    assert t.calls==[]


@pytest.mark.parametrize("response_id",["unknown","resp_../other","resp_a1/../other",
                                         "resp_a1%2fcancel","resp_a1?x=1","", "a"*129])
def test_unknown_or_unsafe_get_never_sends_network(response_id):
    a,t=adapter()
    with pytest.raises((ValueError,PermissionError)):
        a.get_response(response_id)
    assert t.calls==[]


@pytest.mark.parametrize("response_id",["unknown","resp_a1/../x","resp_a1%2fcancel",""])
def test_unknown_or_unsafe_cancel_never_sends_network(response_id):
    a,t=adapter()
    with pytest.raises((ValueError,PermissionError)):
        a.cancel_canary(response_id,explicit_opt_in=True,harness_id="h-1")
    assert t.calls==[]


def test_empty_allowlist_denies_even_if_two_gates_true():
    a,t=adapter(allow=())
    with pytest.raises(PermissionError,match="allowlisted"):
        submit(a)
    assert t.calls==[]


def test_canary_disabled_never_mutates():
    a,t=adapter(enabled=False)
    with pytest.raises(PermissionError,match="disabled"):
        submit(a)
    assert t.calls==[]


def test_owned_lifecycle_submission_poll_cancel_all_pass():
    a,t=adapter()
    r=submit(a)
    assert r.status=="in_progress" and r.input_tokens==10 and r.output_tokens==0
    assert a._owned_responses=={"resp_a1":"h-1"}
    assert a.get_response(r.response_id).status=="completed"
    assert a.cancel_canary(r.response_id,explicit_opt_in=True,harness_id="h-1").status=="cancelled"
    assert [method for method,_,_ in t.calls]==["POST","GET","POST"]


def test_other_harness_cannot_cancel_owned_response():
    a,t=adapter(allow=("h-1","h-2"))
    submit(a)
    before=list(t.calls)
    with pytest.raises(PermissionError,match="selected harness"):
        a.cancel_canary("resp_a1",explicit_opt_in=True,harness_id="h-2")
    assert t.calls==before


def test_other_adapter_instance_cannot_cancel_or_poll():
    a,t=adapter()
    submit(a)
    other,_=adapter(t)
    with pytest.raises(PermissionError,match="not owned"):
        other.get_response("resp_a1")
    with pytest.raises(PermissionError,match="not owned"):
        other.cancel_canary("resp_a1",explicit_opt_in=True,harness_id="h-1")
    assert len(t.calls)==1


@pytest.mark.parametrize("status",["running","queued","done","unknown",""])
def test_unknown_uHP_states_are_refused(status):
    with pytest.raises(ValueError,match="unknown lifecycle status"):
        HarnessRouterAdapter._receipt({"id":"resp_a1","status":status})


@pytest.mark.parametrize("status",["in_progress","completed","failed","incomplete","cancelled"])
def test_every_uHP_status_supported_with_failure_error(status):
    payload={"id":"resp_a1","status":status}
    if status=="failed":
        payload["error"]={"code":"agent_failed","message":"synthetic"}
    assert HarnessRouterAdapter._receipt(payload).status==status


def test_failed_requires_error_not_masked():
    with pytest.raises(ValueError,match="requires an error"):
        HarnessRouterAdapter._receipt({"id":"resp_a1","status":"failed"})


@pytest.mark.parametrize("v",[True,False,-1,1.5,"25",[],{}])
def test_invalid_token_usage_is_refused(v):
    for name in ("input_tokens","output_tokens","total_tokens"):
        with pytest.raises(ValueError,match="invalid token count"):
            HarnessRouterAdapter._receipt({"id":"resp_a1","status":"completed",
                                            "usage":{name:v}})


def test_valid_zero_and_unknown_token_counts():
    r=HarnessRouterAdapter._receipt({"id":"resp_a1","status":"completed",
                                    "usage":{"input_tokens":0,"output_tokens":5}})
    assert r.input_tokens==0 and r.output_tokens==5 and r.total_tokens is None


@pytest.mark.parametrize("x",[False,1,{"n":1},"../path","a b",""])
def test_invalid_or_noncanoical_response_id(x):
    with pytest.raises(ValueError,match="canonical"):
        HarnessRouterAdapter._receipt({"id":x,"status":"completed"})


def test_response_mismatches_requested_get_is_refused():
    stub=Stub({("GET","/v1/responses/resp_a1"):
               {"id":"resp_other","status":"completed"}})
    a,_=adapter(stub)
    submit(a)
    with pytest.raises(ValueError,match="different response id"):
        a.get_response("resp_a1")


def test_response_mismatch_on_cancel_is_refused():
    stub=Stub({("POST","/v1/responses/resp_a1/cancel"):
               {"id":"resp_other","status":"cancelled"}})
    a,_=adapter(stub)
    submit(a)
    with pytest.raises(ValueError,match="different response id"):
        a.cancel_canary("resp_a1",explicit_opt_in=True,harness_id="h-1")


def test_model_substitution_fails_without_receipt():
    stub=Stub({("POST","/v1/responses"):
               {"id":"resp_a1","status":"in_progress","model":"surprise-model"}})
    a,_=adapter(stub)
    with pytest.raises(ValueError,match="complete fallback receipt"):
        submit(a)
    assert a._owned_responses=={}


def test_model_substitution_requires_fallback_attribution():
    stub=Stub({("POST","/v1/responses"):
               {"id":"resp_a1","status":"in_progress","model":"fallback-model",
                "metadata":{"model_fallback":True,"requested_model":"fixture-model",
                            "model_fallback_reason":"temporarily unavailable"}}})
    a,_=adapter(stub)
    r=submit(a)
    assert r.model=="fallback-model"
    assert a._owned_responses=={"resp_a1":"h-1"}


def test_hidden_http_error_body_never_echoed(monkeypatch):
    secret="SYNTHETIC-SECRET-DO-NOT-ECHO"
    def refuse(req, timeout):
        raise error.HTTPError(req.full_url,403,"Forbidden",{},BytesIO(
            ("Authorization: "+secret).encode()))
    monkeypatch.setattr("assistx.harnessrouter_adapter.request.urlopen",refuse)
    a,_=adapter()
    with pytest.raises(RuntimeError,match="HTTP 403") as exc:
        a._http_request("GET","/v1/harnesses",None)
    assert secret not in str(exc.value)


def test_raw_receipt_does_not_appear_in_repr():
    r=HarnessRouterAdapter._receipt({"id":"resp_a1","status":"failed",
                                    "error":{"message":"SYNTHETIC-FIXTURE-SECRET"},
                                    "output":[{"text":"SYNTHETIC-FIXTURE-SECRET"}]})
    assert "SYNTHETIC-FIXTURE-SECRET" not in repr(r)


def test_bad_metadata_and_usage_types_rejected():
    for key in ("metadata","usage"):
        with pytest.raises(ValueError):
            HarnessRouterAdapter._receipt({"id":"resp_a1","status":"completed",key:[]})


def test_opt_in_false_rejects_prior_to_network():
    a,t=adapter()
    with pytest.raises(PermissionError):
        a.submit_canary("synthetic",harness_id="h-1",model="fixture-model",explicit_opt_in=False)
    assert t.calls==[]
