"""Synthetic offline acceptance regressions; no OpenCode or API invocations."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/free_provider_terminal_gate.py"
spec=importlib.util.spec_from_file_location("free_terminal",MODULE)
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
MODEL="cohere/north-mini-code:free"
def example(*,input_tokens=1000,output_tokens=200,reasoning=15,steps=1,tools=1,error=None):
    stream=[{"type":"text","part":{"text":"REPORT"}}]
    for _ in range(steps):
        stream.append({"type":"step_finish","part":{"tokens":{
            "input":input_tokens,"output":output_tokens,"reasoning":reasoning}}})
    stream.extend({"type":"tool_use"} for _ in range(tools))
    if error is not None:stream.append({"type":"error","error":{"data":{"statusCode":error}}})
    stdout="".join(json.dumps(s,sort_keys=True)+"\n" for s in stream).encode()
    stderr=b""
    usage={"input":steps*input_tokens,"output":steps*output_tokens,
           "reasoning":steps*reasoning}
    manifest={
        "schema":"subagent-run-manifest/v1","provider":"kilo_free","session_id":"ses_fixture",
        "requested_model":MODEL,"effective_model":MODEL,"model_identity_complete":True,
        "status":"completed","failure_class":None,"last_finish_reason":"stop",
        "cost":0.0,"tokens":usage,
        "stdout":{"sha256":hashlib.sha256(stdout).hexdigest(),"bytes":len(stdout)},
        "stderr":{"sha256":hashlib.sha256(stderr).hexdigest(),"bytes":len(stderr)}
    }
    return manifest, stdout, stderr
def run(m,s,e,**limits):
    return gate.assess(json.dumps(m).encode(),s,e,expected_model=limits.pop("model",MODEL),
       max_input=limits.pop("input",35000),max_output=limits.pop("output",1800),
       max_steps=limits.pop("steps",3),max_tools=limits.pop("tools",3))
def test_valid_receipt_not_billing_proof():
    report=run(*example())
    assert report["status"]=="LOCAL_RECEIPT_PASS"
    assert report["upstream_billing_verified"] is False
    assert report["production_authorized"] is False
    assert report["output_quality_verified"] is False
    assert report["independent_archive_verified"] is False
@pytest.mark.parametrize("value,limit,reason", [
    (1964,1800,"budget_output"),(35001,35000,"budget_input")
])
def test_real_world_budget_race(value,limit,reason):
    kwargs={"output_tokens":value} if reason=="budget_output" else {"input_tokens":value}
    m,s,e=example(**kwargs)
    assert reason in run(m,s,e)["reasons"]
@pytest.mark.parametrize("field,count,reason", [
    ("steps",4,"budget_steps"),("tools",4,"budget_tools")
])
def test_step_and_tool_overrun(field,count,reason):
    kwargs={field:count}
    m,s,e=example(**kwargs)
    assert reason in run(m,s,e)["reasons"]
@pytest.mark.parametrize("code", [401,402,403,429,503,"unknown"])
def test_all_errors_fail_closed(code):
    m,s,e=example(error=code)
    assert "trace_provider_error" in run(m,s,e)["reasons"]
def test_missing_text_denied():
    m,s,e=example()
    s=s.replace(b'"type": "text"',b'"type": "unknown"')
    m["stdout"]={"sha256":gate.canonical_sha256(s),"bytes":len(s)}
    assert "trace_no_text" in run(m,s,e)["reasons"]
def test_manifest_model_swap():
    m,s,e=example()
    m["effective_model"]="paid/different"
    assert "manifest_model_mismatch" in run(m,s,e)["reasons"]
def test_paid_model_denied_even_matching_manifest():
    m,s,e=example()
    m["requested_model"]=m["effective_model"]="paid/not-free"
    assert "not_exact_free_model" in run(m,s,e,model="paid/not-free")["reasons"]
def test_missing_final_stop():
    m,s,e=example()
    m["last_finish_reason"]="tool-calls"
    assert "manifest_missing_stop" in run(m,s,e)["reasons"]
def test_forged_cost():
    m,s,e=example()
    m["cost"]=0.01
    assert "reported_cost_nonzero_or_missing" in run(m,s,e)["reasons"]
def test_mismatched_reported_token_count():
    m,s,e=example()
    m["tokens"]["output"]+=1
    assert "manifest_output_token_mismatch" in run(m,s,e)["reasons"]
def test_wrong_stdout_digest():
    m,s,e=example()
    assert "stdout_receipt_mismatch" in run(m,s+b"tampered",e)["reasons"]
def test_wrong_stderr_digest():
    m,s,e=example()
    assert "stderr_receipt_mismatch" in run(m,s,b"unwitnessed")["reasons"]
def test_missing_step():
    m,s,e=example(steps=0)
    assert "trace_no_finished_step" in run(m,s,e)["reasons"]
def test_invalid_json_event():
    m,s,e=example()
    s+=b'not json\n'
    m["stdout"]={"sha256":gate.canonical_sha256(s),"bytes":len(s)}
    assert "trace_invalid_json" in run(m,s,e)["reasons"]
def test_invalid_negative_token():
    m,s,e=example()
    s=s.replace(b'"output": 200',b'"output": -1')
    m["stdout"]={"sha256":gate.canonical_sha256(s),"bytes":len(s)}
    assert "trace_invalid_token_output" in run(m,s,e)["reasons"]
def test_missing_manifest_is_rejected():
    m,s,e=example()
    assert "manifest_invalid_json" in gate.assess(b"{",s,e,expected_model=MODEL,
       max_input=35000,max_output=1800,max_steps=3,max_tools=3)["reasons"]
def test_zero_or_unknown_limits_fail_closed():
    m,s,e=example()
    assert "invalid_limit" in run(m,s,e,output=-1)["reasons"]