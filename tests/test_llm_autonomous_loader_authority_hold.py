"""Fail-closed network-negative controls for autonomous loader code paths."""
import pytest
from assistx.llm import client as llm


@pytest.fixture
def no_effects(monkeypatch):
    calls = []
    def fail(kind):
        def inner(*a, **kw):
            calls.append(kind)
            pytest.fail("unverified external effect: " + kind)
        return inner
    monkeypatch.setattr(llm.requests, "post", fail("post"))
    monkeypatch.setattr(llm.requests, "get", fail("get"))
    monkeypatch.setattr(llm.time, "sleep", fail("sleep"))
    monkeypatch.setattr(llm, "_loader_native_load", fail("native_load"))
    monkeypatch.setattr(llm, "_loader_native_unload", fail("native_unload"))
    monkeypatch.setattr(llm, "_loader_write_kg", fail("kg_write"))
    return calls


@pytest.mark.parametrize("method,expected", [
    ("warmup", {"ok": False, "tps": None, "latency_s": None,
                "reason": "unverified_resource_owned_generation_admission"}),
    ("wait_hot", False),
    ("current_hot", []),
    ("load", False),
])
def test_autonomous_egress_paths_fail_without_network_or_kg(method, expected, no_effects):
    calls = {
        "warmup": lambda: llm._loader_warmup("http://fake.invalid/v1", "fixture-model"),
        "wait_hot": lambda: llm._loader_wait_hot("http://fake.invalid/v1", "fixture-model", 100),
        "current_hot": lambda: llm._loader_current_hot("http://fake.invalid/v1"),
        "load": lambda: llm._loader_load("http://fake.invalid/v1", "fixture-model"),
    }
    assert calls[method]() == expected
    assert no_effects == []


def test_direct_background_loop_does_not_poll_or_mutate_state(monkeypatch, no_effects):
    monkeypatch.setattr(llm, "_refresh_fleet_nodes", lambda: pytest.fail("discovery loop"))
    baseline = dict(llm._loader_state)
    assert llm._fleet_loader_loop() is None
    assert llm._loader_state == baseline
    assert no_effects == []


@pytest.mark.parametrize("disable", ["0", "1", "", "false"])
def test_timer_env_does_not_grant_autonomous_loader_start(monkeypatch, no_effects, disable):
    monkeypatch.setenv("LLM_LOADER_DISABLE", disable)
    monkeypatch.setattr(llm.threading, "Thread", lambda *a, **kw: pytest.fail("thread start"))
    before = llm._loader_thread
    assert llm.start_fleet_loader() is None
    assert llm._loader_thread is before
    assert no_effects == []


def test_read_only_library_and_residency_discovery_still_exists():
    # These are separate GET-only inventory functions, not model generation.
    assert callable(llm._probe_on_device_models)
    assert callable(llm._probe_loaded_models)


def test_autonomous_loader_has_no_env_opt_in():
    assert llm._loader_warmup("not-a-network-endpoint", "model")["ok"] is False
    assert llm._loader_wait_hot("not-a-network-endpoint", "model", 0) is False
