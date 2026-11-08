"""Socketless exact-origin tests for three opt-in local-only inference probes."""

from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/fleet-local-capacity-nonadmitting-canary.py"
spec = importlib.util.spec_from_file_location("fleet_local_canary", SOURCE)
assert spec and spec.loader
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)
MODEL = "ornith-1.5-35b-a3b-apex-mtp"


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, count):
        return self.body[:count]


class Opener:
    def __init__(self, *, model=MODEL, count=2, loaded=True, huge=False, content="OK", returned_model=None):
        self.calls = []
        self.lock = threading.Lock()
        self.model = model
        self.count = count
        self.loaded = loaded
        self.huge = huge
        self.content = content
        self.returned_model = returned_model

    def open(self, req, timeout):
        with self.lock:
            self.calls.append((req.full_url, req.get_method(), timeout, req.data, dict(req.header_items())))
        if req.full_url == canary.CATALOG:
            return Response(
                json.dumps({"data": [{"id": self.model, "state": "loaded" if self.loaded else "unloaded"}]}).encode()
            )
        if req.full_url == canary.GENERATE:
            body = json.loads(req.data)
            assert body["max_tokens"] == canary.MAX_TOKENS
            assert body["stream"] is False
            assert body["model"] == MODEL
            assert len(body["messages"]) == 1
            obj = {"choices": [{"message": {"content": self.content}}], "usage": {"completion_tokens": self.count}}
            if self.returned_model is not None:
                obj["model"] = self.returned_model
            return Response((b"x" * (canary.MAX_RESPONSE_BYTES + 1)) if self.huge else json.dumps(obj).encode())
        pytest.fail("unpinned endpoint")


def test_no_live_flag_cannot_issue_network_or_authorize(monkeypatch, capsys):
    monkeypatch.setattr(canary, "_opener", lambda: pytest.fail("network in default mode"))
    assert canary.main([]) == 2
    d = json.loads(capsys.readouterr().out)
    assert d["status"] == "HOLD" and d["task_admission"] == "HOLD"


def test_three_bounded_local_requests_no_admission():
    client = Opener()
    result = canary.run_canary(MODEL, client_factory=lambda: client)
    assert result["client_parallel_requests_completed"] is True
    assert result["physical_parallel_pair_observed"] is False
    assert result["server_parallel_processing_verified"] is False
    assert result["approved_parallel_slots"] == 0
    assert result["rollback_verified"] is False
    assert result["owner_signed_projection_verified"] is False
    assert result["task_admission"] == "HOLD"
    assert len(result["requests"]) == 3
    assert [call[1] for call in client.calls].count("GET") == 2
    assert [call[1] for call in client.calls].count("POST") == 3
    assert all(call[0] in (canary.CATALOG, canary.GENERATE) for call in client.calls)
    assert all(call[2] in (4, 16) for call in client.calls)
    assert all("Authorization" not in call[4] for call in client.calls)


def test_loaded_model_must_match_exact_identity():
    client = Opener(model="some-other-model")
    with pytest.raises(ValueError, match="loaded_model_identity_mismatch"):
        canary.run_canary(MODEL, client_factory=lambda: client)
    assert all(call[1] == "GET" for call in client.calls)


def test_unloaded_model_is_not_started():
    client = Opener(loaded=False)
    with pytest.raises(ValueError, match="loaded_model_identity_mismatch"):
        canary.run_canary(MODEL, client_factory=lambda: client)
    assert len(client.calls) == 1


@pytest.mark.parametrize("bad_id", ["", "x\nrogue", "x\x00bad", "x" * 161])
def test_bad_model_identity_rejected_before_network(bad_id):
    with pytest.raises(ValueError, match="model_identity_invalid"):
        canary.run_canary(bad_id, client_factory=lambda: pytest.fail("network"))


def test_more_than_budget_completion_tokens_does_not_count_as_pass():
    client = Opener(count=canary.MAX_TOKENS + 1)
    with pytest.raises(ValueError, match="completion_budget_unverified"):
        canary.run_canary(MODEL, client_factory=lambda: client)


def test_unbounded_response_rejected():
    client = Opener(huge=True)
    with pytest.raises(ValueError, match="response_exceeds_limit"):
        canary.run_canary(MODEL, client_factory=lambda: client)


def test_unpinned_url_rejected_without_opening_socket():
    request = canary.urllib.request.Request("https://host.invalid/anything", method="GET")
    with pytest.raises(ValueError, match="origin_not_loopback_pinned"):
        canary._json_response(Opener(), request, 4)


def test_redirect_refused():
    assert canary._NoRedirect().redirect_request(None, None, 302, "Moved", {}, "http://outside.invalid") is None


def test_main_error_still_exits_hold(monkeypatch, capsys):
    def error(*_args, **_kwargs):
        raise ValueError("synthetic credential like secret")

    monkeypatch.setattr(canary, "run_canary", error)
    assert canary.main(["--live"]) == 2
    output = capsys.readouterr().out
    assert "secret" not in output
    assert json.loads(output)["approved_parallel_slots"] == 0


def test_main_success_still_exits_hold(monkeypatch, capsys):
    monkeypatch.setattr(
        canary,
        "run_canary",
        lambda *_args, **_kwargs: {
            "status": "HOLD",
            "task_admission": "HOLD",
            "approved_parallel_slots": 0,
            "client_parallel_requests_completed": True,
            "physical_parallel_pair_observed": False,
            "server_parallel_processing_verified": False,
        },
    )
    assert canary.main(["--live"]) == 2
    assert json.loads(capsys.readouterr().out)["task_admission"] == "HOLD"


@pytest.mark.parametrize("bad_count", [0, -1, 65, False, True, 1.0, "2"])
def test_zero_malformed_or_out_of_budget_tokens_fail_closed(bad_count):
    client = Opener(count=bad_count)
    with pytest.raises(ValueError, match="completion_budget_unverified"):
        canary.run_canary(MODEL, client_factory=lambda: client)


@pytest.mark.parametrize("bad_content", [None, "", " \t ", 0, [], {}])
def test_empty_or_nontext_completion_is_not_capacity_evidence(bad_content):
    client = Opener(content=bad_content)
    with pytest.raises(ValueError, match="nonproductive_completion"):
        canary.run_canary(MODEL, client_factory=lambda: client)


def test_served_model_mismatch_fails_even_when_catalog_is_unchanged():
    client = Opener(returned_model="different-loaded-model")
    with pytest.raises(ValueError, match="served_model_identity_mismatch"):
        canary.run_canary(MODEL, client_factory=lambda: client)


def test_verified_same_model_in_response_is_accepted_but_not_admitted():
    client = Opener(returned_model=MODEL)
    result = canary.run_canary(MODEL, client_factory=lambda: client)
    assert result["client_parallel_requests_completed"] is True
    assert result["physical_parallel_pair_observed"] is False
    assert result["approved_parallel_slots"] == 0


@pytest.mark.parametrize(
    "category",
    [
        "nonproductive_completion",
        "served_model_identity_mismatch",
        "completion_budget_unverified",
        "loaded_model_identity_mismatch",
    ],
)
def test_allowlisted_fleet_capacity_rejection_codes_are_safe(category, monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise ValueError(category)

    monkeypatch.setattr(canary, "run_canary", fail)
    assert canary.main(["--live"]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["reason"] == category.upper()
    assert body["task_admission"] == "HOLD"
    assert body["approved_parallel_slots"] == 0


def test_arbitrary_error_messages_must_not_leak_into_output(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise ValueError("sensitive synthetic private user prompt or node secret")

    monkeypatch.setattr(canary, "run_canary", fail)
    assert canary.main(["--live"]) == 2
    output = capsys.readouterr().out
    assert "synthetic private" not in output and "secret" not in output
    assert json.loads(output)["reason"] == "LOCAL_CAPACITY_EVIDENCE_INCOMPLETE"


def test_reasoning_model_30_token_completion_is_within_bounded_64_token_budget():
    client = Opener(count=30, content="OK", returned_model=MODEL)
    result = canary.run_canary(MODEL, client_factory=lambda: client)
    assert len(result["requests"]) == 3
    assert all(item["completion_tokens"] == 30 for item in result["requests"])
    assert result["completion_token_budget_per_request"] == 64
    assert result["client_parallel_requests_completed"] is True
    assert result["server_parallel_processing_verified"] is False
    assert result["approved_parallel_slots"] == 0
