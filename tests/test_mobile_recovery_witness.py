from __future__ import annotations

import importlib.util
import pathlib

import pytest

path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mobile_recovery_witness.py"
spec = importlib.util.spec_from_file_location("mobile_recovery_witness", path)
assert spec and spec.loader
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def stub(mapping):
    def fetch(origin, path, timeout):
        return mapping.get((origin, path), (None, None))
    return fetch


def test_gateway_health_does_not_imply_admission():
    base = "https://gateway.example:8443"
    data = {
        (base, "/health"): (200, {"status": "ok"}),
        (base, "/api/v1/runtime/catalog"): (200, {
            "schema_version": "2", "agent_auto_available": False,
            "agent_runtime_count": 0, "fleet_runtime_count": 0,
        })
    }
    result = w.gateway_status(base, fetch=stub(data))
    assert result["state"] == "projection_not_admitted"
    assert result["agent_auto_admitted"] is False


@pytest.mark.parametrize("code,expected", [(None, "catalog_transport_error"), (401,"identity_required"), (500, "catalog_http_error")])
def test_gateway_avoids_false_positive_on_catalog_failure(code,expected):
    base="https://gateway.example:8443"
    result=w.gateway_status(base,fetch=stub({
        (base,"/health"):(200, {}),
        (base,"/api/v1/runtime/catalog"):(code, None),
    }))
    assert result["state"]==expected
    assert not result["agent_auto_admitted"]


def test_approved_gateway_requires_positive_agent_count():
    base="https://gateway.example:8443"
    result=w.gateway_status(base,fetch=stub({
        (base,"/health"):(200, {}),
        (base,"/api/v1/runtime/catalog"):(200,{
            "schema_version":"2","agent_auto_available":True,"agent_runtime_count":2,"fleet_runtime_count":3}),
    }))
    assert result["state"]=="admitted"
    assert result["agent_runtime_count"]==2


def test_lmstudio_inventory_never_assumed_resident():
    base="http://model.example:1234"
    data={(base,"/api/v1/models"):(200,{"models":[
        {"type":"llm","key":"cold","loaded_instances":[]},
        {"type":"embedding","key":"embedding","loaded_instances":[{"id":"a"}]},
        {"type":"llm","key":"hot","loaded_instances":[{"id":"ok"}]}]})}
    result=w.provider_status(base,fetch=stub(data))
    assert result["models_listed"]==2
    assert result["confirmed_loaded"]==1


@pytest.mark.parametrize("path,health,alias,slots,expected", [
    ("/models/k2.gguf","ok","k2-1b",4,1),
    ("","ok","k2-1b",4,0),
    ("/models/k2.gguf","loading","k2-1b",4,0),
    ("/models/k2.gguf","ok","wrong",4,0),
    ("/models/k2.gguf","ok","k2-1b",0,0),
])
def test_compatible_provider_requires_health_and_props(path,health,alias,slots,expected):
    base="http://llama.example:1235"
    sample={
        (base,"/v1/models"):(200,{"data":[{"id":"k2-1b"}]}),
        (base,"/health"):(200,{"status":health}),
        (base,"/props"):(200,{"model_alias":alias,"model_path":path,"total_slots":slots})
    }
    result=w.provider_status(base,fetch=stub(sample))
    assert result["confirmed_loaded"]==expected
    assert "model_path" not in str(result)


@pytest.mark.parametrize("url",[
    "https://username:secret@gateway.example:8443",
    "https://gateway.example:8443/?secret=1",
    "file:///tmp/config.json",
    "http://gateway.example:8443",
])
def test_unsafe_gateway_url_rejected(url):
    with pytest.raises(ValueError): w.origin(url,https_only=True)


def test_witness_redacts_models_and_no_requests_mutate():
    gateway="https://gateway.example:8443"
    provider="http://llama.example:1235"
    calls=[]
    def fetch(origin,path,timeout):
        calls.append((origin,path))
        if path=="/health":return 200,{"status":"ok"}
        if path=="/api/v1/runtime/catalog":
            return 200,{"schema_version":"2","agent_auto_available":False,"agent_runtime_count":0}
        if path=="/v1/models":return 200,{"data":[{"id":"secret-key-should-not-be-saved"}]}
        if path=="/props":return 200,{"model_alias":"secret-key-should-not-be-saved","model_path":"/private/file","total_slots":1}
        return 404,None
    result=w.observe(gateway,[provider,provider],fetch=fetch)
    assert len(result["provider_witnesses"])==1
    assert result["provider_witnesses"][0]["confirmed_loaded"]==1
    assert "secret-key" not in str(result)
    assert "private/file" not in str(result)
    assert result["record_sha256"]
    assert set(p for _,p in calls) <= {
        "/health","/api/v1/runtime/catalog","/api/v1/models","/v1/models","/props"
    }
