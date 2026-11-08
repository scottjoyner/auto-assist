"""No-network regressions: physical overlap is not signed Task capacity."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import threading

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/lfm3_server_slot_overlap_preflight.py"
spec = importlib.util.spec_from_file_location("lfm3_slots", SOURCE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def samples(*values):
    return [{"id": i, "is_processing": v} for i, v in enumerate(values)]


def generation(request_no=1, n=96):
    return {
        "request": request_no,
        "http": 200,
        "completion_tokens": n,
        "duration_ms": 2000,
        "final_content_nonblank": True,
    }


def test_independent_two_busy_slot_trace_passes_physical_but_never_admits():
    result = m.interpret_observations([generation(1), generation(2)], [0, 1, 2, 2, 1, 0], 0)
    assert result["peak_simultaneously_busy_server_slots"] == 2
    assert result["independently_observed_server_overlap"] is True
    assert result["physical_evidence"] == "PASS_TWO_SERVER_SLOTS"
    assert result["approved_task_slots"] == 0
    assert result["owner_signed_projection_verified"] is False
    assert result["sustained_throughput_slo_verified"] is False
    assert result["status"] == "HOLD" and result["task_admission"] == "HOLD"
    assert "secret-generated-answer" not in json.dumps(result)
    assert '"message"' not in json.dumps(result)


@pytest.mark.parametrize("trace,after", [([0, 1, 1, 0], 0), ([0, 2, 2], 1), ([1, 1, 1], 0)])
def test_no_concurrent_server_trace_or_nonidle_tail_never_claims_physical_pass(trace, after):
    result = m.interpret_observations([generation(1), generation(2)], trace, after)
    assert result["physical_evidence"] == "HOLD_OVERLAP_UNVERIFIED"
    assert result["independently_observed_server_overlap"] is False
    assert result["approved_task_slots"] == 0


@pytest.mark.parametrize("trace,after", [([], 0), ([False, 1], 0), ([0, 5], 0), ([-1, 2], 0), ([0, 2], True)])
def test_malformed_slot_trace_fails_closed(trace, after):
    with pytest.raises(ValueError, match="incomplete_slot_trace"):
        m.interpret_observations([generation(1), generation(2)], trace, after)


@pytest.mark.parametrize("n", [0, -1, 257, False, 1.0, "96"])
def test_bad_completion_count_not_physical_capacity_evidence(n):
    with pytest.raises(ValueError, match="incomplete_generation_evidence"):
        m.interpret_observations([generation(1, n), generation(2)], [0, 2], 0)


def test_missing_generation_not_promotable():
    with pytest.raises(ValueError, match="incomplete_slot_trace"):
        m.interpret_observations([generation(1)], [0, 2], 0)


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [{}],
        samples(False, False, False),
        samples(False, False, False, 0),
        {"items": samples(False, False, False, False)},
    ],
)
def test_slot_counter_requires_exact_four_real_booleans(bad):
    with pytest.raises(ValueError, match="unverified_server_slots"):
        m._parse_busy_slots(bad)


def test_slot_counter_real_busy_worker_count():
    assert m._parse_busy_slots(samples(True, True, False, False)) == 2
    assert m._parse_busy_slots(samples(False, False, False, False)) == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"choices": [], "usage": {}},
        {"choices": [{"message": {"content": " "}}], "usage": {"completion_tokens": 96}},
        {"choices": [{"message": {"content": "OK"}}], "usage": {"completion_tokens": 0}},
        {"choices": [{"message": {"content": "OK"}}], "usage": {"completion_tokens": True}},
    ],
)
def test_bad_local_response_does_not_count_as_generating(bad):
    with pytest.raises(ValueError):
        m._parse_completion(bad, 1, 50)


def test_wrong_model_response_fails_closed():
    val = {"model": "different-model", "choices": [{"message": {"content": "OK"}}], "usage": {"completion_tokens": 20}}
    with pytest.raises(ValueError, match="incorrect_served_model"):
        m._parse_completion(val, 1, 50)


def test_exact_model_nonblank_completion_only_yields_metadata():
    val = {
        "model": m.MODEL,
        "choices": [{"message": {"content": "sensitive synthetic generated text"}}],
        "usage": {"completion_tokens": 80},
    }
    result = m._parse_completion(val, 2, 2000)
    assert result == {
        "request": 2,
        "http": 200,
        "completion_tokens": 80,
        "duration_ms": 2000,
        "final_content_nonblank": True,
    }
    assert "sensitive" not in json.dumps(result)


class FakeResponse:
    status = 200

    def __init__(self, d):
        self.data = json.dumps(d).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n):
        return self.data[:n]


class FakeOpener:
    def __init__(self):
        self.calls = []

    def open(self, req, timeout):
        self.calls.append((req.full_url, req.get_method(), timeout, req.data))
        if req.full_url.endswith("/slots"):
            return FakeResponse(samples(False, False, False, False))
        if req.full_url.endswith("/health"):
            return FakeResponse({"status": "ok"})
        if req.full_url.endswith("/v1/models"):
            return FakeResponse({"data": [{"id": m.MODEL}]})
        return FakeResponse(
            {"model": m.MODEL, "choices": [{"message": {"content": "OK"}}], "usage": {"completion_tokens": 80}}
        )


def test_pinned_local_endpoints_and_request_bound():
    fake = FakeOpener()
    assert m._http_json("/slots", opener=fake) == samples(False, False, False, False)
    assert fake.calls == [(m.ORIGIN + "/slots", "GET", 3, None)]
    with pytest.raises(ValueError, match="not_a_pinned_local_route"):
        m._http_json("https://elsewhere.invalid/", opener=fake)
    with pytest.raises(ValueError, match="wrong_upstream_method"):
        m._http_json("/v1/chat/completions", opener=fake)


def test_fixed_generation_uses_only_pinned_local_model():
    fake = FakeOpener()
    orig = m._http_json

    def proxy(route, *, body=None, opener=None):
        return orig(route, body=body, opener=fake)

    m._http_json = proxy
    try:
        result = m._generate(1, threading.Barrier(1))
    finally:
        m._http_json = orig
    assert result["completion_tokens"] == 80
    assert result["final_content_nonblank"] is True
    assert fake.calls[0][0] == m.ORIGIN + "/v1/chat/completions"
    assert fake.calls[0][1] == "POST"
    payload = json.loads(fake.calls[0][3])
    assert payload["model"] == m.MODEL and payload["max_tokens"] == 256
    assert payload["stream"] is False


def test_default_never_contacts_host_model(monkeypatch, capsys):
    monkeypatch.setattr(m, "run_live", lambda: pytest.fail("default mode may not run model"))
    assert m.main([]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["task_admission"] == "HOLD"


def test_failure_never_exposes_untrusted_exception_text(monkeypatch, capsys):
    def no(*_):
        raise ValueError("synthetic secret user prompt")

    monkeypatch.setattr(m, "run_live", no)
    assert m.main(["--live"]) == 2
    output = capsys.readouterr().out
    assert "synthetic secret" not in output
    assert json.loads(output)["approved_task_slots"] == 0


def test_live_success_still_exits_hold(monkeypatch, capsys):
    monkeypatch.setattr(
        m, "run_live", lambda: m.interpret_observations([generation(1), generation(2)], [0, 1, 2, 2, 0], 0)
    )
    assert m.main(["--live"]) == 2
    obj = json.loads(capsys.readouterr().out)
    assert obj["physical_evidence"] == "PASS_TWO_SERVER_SLOTS"
    assert obj["task_admission"] == "HOLD" and obj["approved_task_slots"] == 0


def test_reasoning_aware_lfm3_final_budget_allows_256_tokens():
    assert m.MAX_OUTPUT_TOKENS == 256
    valid = {"model": m.MODEL, "choices": [{"message": {"content": "OK"}}], "usage": {"completion_tokens": 99}}
    result = m._parse_completion(valid, 1, 2000)
    assert result["completion_tokens"] == 99
    assert result["final_content_nonblank"] is True
