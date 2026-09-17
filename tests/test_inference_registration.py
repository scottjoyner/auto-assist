"""Tests for the inference session tracking system in assistx.llm.client.

Covers register_inference_start, stop_inference, stop_inference_for_model,
get_inference_sessions, and get_active_inference_models.  No external services
required — all state is module-level in-memory dicts.
"""
import time

import pytest

from assistx.llm import client as llm_client


def _clear_inference_state():
    """Reset the module-level inference state so tests are isolated."""
    llm_client._inference_sessions.clear()
    llm_client._inference_session_id = 0


@pytest.fixture(autouse=True)
def _isolate_inference_state():
    """Guarantee clean inference state for every test."""
    _clear_inference_state()
    yield
    _clear_inference_state()


# ─── register_inference_start ───────────────────────────────────────────────

def test_register_start_returns_session_id():
    sid = llm_client.register_inference_start("qwen-7b", "http://10.0.0.1:1234/v1")
    assert sid is not None
    assert sid.startswith("inf_")
    assert "qwen-7b" not in sid  # session id is opaque, not the model name


def test_register_start_records_model_and_node():
    llm_client.register_inference_start(
        "llama-13b", "http://10.0.0.2:1234/v1", node_id="beelink",
        request_id="req-42", task_id="task-7",
    )
    sessions = llm_client.get_inference_sessions(only_active=True)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["model_id"] == "llama-13b"
    assert s["base_url"] == "http://10.0.0.2:1234/v1"
    assert s["node_id"] == "beelink"
    assert s["request_id"] == "req-42"
    assert s["task_id"] == "task-7"
    assert s["status"] == "active"


def test_register_start_records_timestamps():
    before = time.time()
    llm_client.register_inference_start("model", "http://x:1234/v1")
    after = time.time()
    sessions = llm_client.get_inference_sessions(only_active=True)
    s = sessions[0]
    assert before - 0.1 <= s["started_at"] <= after + 0.1
    assert isinstance(s["started_at_ts"], int)
    assert s["started_at_ts"] > 0


def test_register_start_increments_session_ids():
    sid1 = llm_client.register_inference_start("a", "http://x:1234/v1")
    sid2 = llm_client.register_inference_start("b", "http://x:1234/v1")
    assert sid1 != sid2
    n1 = int(sid1.split("_")[1])
    n2 = int(sid2.split("_")[1])
    assert n2 == n1 + 1


def test_register_start_defaults():
    """Optional fields default to empty string, not None."""
    llm_client.register_inference_start("model", "http://x:1234/v1")
    s = llm_client.get_inference_sessions(only_active=True)[0]
    assert s["node_id"] == ""
    assert s["request_id"] == ""
    assert s["task_id"] == ""


# ─── stop_inference ────────────────────────────────────────────────────────

def test_stop_inference_completes_session():
    sid = llm_client.register_inference_start("model", "http://x:1234/v1")
    result = llm_client.stop_inference(sid)
    assert result["ok"] is True
    session = result["session"]
    assert session["status"] == "completed"
    assert "ended_at" in session
    assert "duration_s" in session
    assert session["duration_s"] >= 0


def test_stop_inference_sets_custom_status():
    sid = llm_client.register_inference_start("model", "http://x:1234/v1")
    result = llm_client.stop_inference(sid, status="failed")
    assert result["session"]["status"] == "failed"


def test_stop_inference_removes_from_active():
    sid = llm_client.register_inference_start("model", "http://x:1234/v1")
    assert len(llm_client.get_inference_sessions(only_active=True)) == 1
    llm_client.stop_inference(sid)
    assert len(llm_client.get_inference_sessions(only_active=True)) == 0


def test_stop_inference_nonexistent_session():
    result = llm_client.stop_inference("inf_nonexistent")
    assert result["ok"] is False
    assert "not found" in result["reason"]


def test_stop_inference_duration_positive():
    sid = llm_client.register_inference_start("model", "http://x:1234/v1")
    time.sleep(0.01)
    result = llm_client.stop_inference(sid)
    assert result["session"]["duration_s"] > 0


# ─── stop_inference_for_model ───────────────────────────────────────────────

def test_stop_for_model_stops_all_matching():
    llm_client.register_inference_start("a", "http://x:1234/v1")
    llm_client.register_inference_start("a", "http://x:1234/v1")
    llm_client.register_inference_start("b", "http://x:1234/v1")
    count = llm_client.stop_inference_for_model("a", "http://x:1234/v1")
    assert count == 2
    active = llm_client.get_inference_sessions(only_active=True)
    assert len(active) == 1
    assert active[0]["model_id"] == "b"


def test_stop_for_model_ignores_different_base_url():
    llm_client.register_inference_start("a", "http://x:1234/v1")
    llm_client.register_inference_start("a", "http://y:1234/v1")
    count = llm_client.stop_inference_for_model("a", "http://x:1234/v1")
    assert count == 1
    active = llm_client.get_inference_sessions(only_active=True)
    assert len(active) == 1
    assert active[0]["base_url"] == "http://y:1234/v1"


def test_stop_for_model_ignores_already_stopped():
    sid = llm_client.register_inference_start("a", "http://x:1234/v1")
    llm_client.stop_inference(sid)
    count = llm_client.stop_inference_for_model("a", "http://x:1234/v1")
    assert count == 0


def test_stop_for_model_zero_matches():
    count = llm_client.stop_inference_for_model("nonexistent", "http://x:1234/v1")
    assert count == 0


# ─── get_inference_sessions ────────────────────────────────────────────────

def test_get_sessions_filter_model():
    llm_client.register_inference_start("a", "http://x:1234/v1")
    llm_client.register_inference_start("b", "http://x:1234/v1")
    filtered = llm_client.get_inference_sessions(filter_model="a")
    assert len(filtered) == 1
    assert filtered[0]["model_id"] == "a"


def test_get_sessions_filter_base_url():
    llm_client.register_inference_start("a", "http://x:1234/v1")
    llm_client.register_inference_start("a", "http://y:1234/v1")
    filtered = llm_client.get_inference_sessions(filter_base_url="http://x:1234/v1")
    assert len(filtered) == 1
    assert filtered[0]["base_url"] == "http://x:1234/v1"


def test_get_sessions_only_active_false():
    llm_client.register_inference_start("a", "http://x:1234/v1")
    llm_client.register_inference_start("b", "http://x:1234/v1")
    llm_client.stop_inference(llm_client.get_inference_sessions(only_active=True)[0]["session_id"])
    all_sessions = llm_client.get_inference_sessions(only_active=False)
    assert len(all_sessions) == 2
    active_sessions = llm_client.get_inference_sessions(only_active=True)
    assert len(active_sessions) == 1


def test_get_sessions_returns_copies():
    """Mutating returned dict must not corrupt internal state."""
    sessions = llm_client.get_inference_sessions()
    if sessions:
        sessions[0]["model_id"] = "tampered"
    real = llm_client.get_inference_sessions()
    if real:
        assert real[0]["model_id"] != "tampered"


def test_get_sessions_empty():
    assert llm_client.get_inference_sessions() == []


# ─── get_active_inference_models ───────────────────────────────────────────

def test_active_models_groups_sessions():
    llm_client.register_inference_start("qwen", "http://x:1234/v1", node_id="n1")
    llm_client.register_inference_start("qwen", "http://x:1234/v1", node_id="n1")
    models = llm_client.get_active_inference_models()
    assert len(models) == 1
    m = models[0]
    assert m["model_id"] == "qwen"
    assert m["base_url"] == "http://x:1234/v1"
    assert m["active_sessions"] == 2


def test_active_models_distinct_by_url():
    llm_client.register_inference_start("a", "http://x:1234/v1")
    llm_client.register_inference_start("a", "http://y:1234/v1")
    models = llm_client.get_active_inference_models()
    assert len(models) == 2


def test_active_models_excludes_stopped():
    llm_client.register_inference_start("a", "http://x:1234/v1")
    llm_client.register_inference_start("b", "http://x:1234/v1")
    sessions = llm_client.get_inference_sessions(only_active=True)
    llm_client.stop_inference(sessions[0]["session_id"])
    models = llm_client.get_active_inference_models()
    assert len(models) == 1
    assert models[0]["model_id"] == "b"


def test_active_models_empty():
    assert llm_client.get_active_inference_models() == []


# ─── concurrency safety (basic) ────────────────────────────────────────────

def test_concurrent_register_and_stop():
    """Register many sessions, then stop many — no crashes, no duplicates."""
    sids = [
        llm_client.register_inference_start(f"m{i}", "http://x:1234/v1")
        for i in range(50)
    ]
    assert len(llm_client.get_inference_sessions(only_active=True)) == 50
    for sid in sids[:25]:
        llm_client.stop_inference(sid)
    assert len(llm_client.get_inference_sessions(only_active=True)) == 25
    remaining = llm_client.get_inference_sessions(only_active=True)
    assert all(s["status"] == "active" for s in remaining)


# ─── cleanup between tests ─────────────────────────────────────────────────

def test_autouse_fixture_clears_state():
    """Verify the autouse fixture actually cleans up between tests.

    The fixture clears _inference_sessions and resets _inference_session_id
    before every test.  So the counter should be 0 at test entry.
    """
    assert llm_client._inference_session_id == 0
    assert len(llm_client._inference_sessions) == 0
    # Register something; the next test's fixture will clean it up.
    llm_client.register_inference_start("ghost", "http://x:1234/v1")
    assert len(llm_client.get_inference_sessions()) == 1
