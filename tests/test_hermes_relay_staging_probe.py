"""No-network tests: isolated staging canary never grants production admission."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "hermes_relay_staging_probe_under_test",
    ROOT / "scripts" / "probe_hermes_relay_staging.py",
)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

_TOKEN = "only-for-synthetic-staging-client-" + "x" * 48
_MODEL = "synthetic-lfm3-v1"
_BODY = (
    b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"READY"}}]}\n\n'
    b"data: [DONE]\n\n"
)


def test_good_synthetic_staging_proof_still_holds(monkeypatch):
    calls = []

    def fake(host, port, *, token, model, stream=True):
        calls.append((host, port, token, model, stream))
        if token != _TOKEN:
            return 401, "application/json", b"{}"
        if model != _MODEL:
            return 403, "application/json", b"{}"
        return 200, "text/event-stream", _BODY

    monkeypatch.setattr(probe, "_request", fake)
    report = probe.probe(
        origin="http://assistx-api:8000", token=_TOKEN, model=_MODEL,
    )
    assert report["observation_complete"]
    assert report["model_text_and_done_verified"]
    assert report["invalid_client_token_denied"]
    assert report["unapproved_model_denied"]
    assert report["production_admission"] == "HOLD"
    assert not report["real_hermes_process_verified"]
    assert not report["ingress_public_path_denial_verified"]
    assert len(calls) == 3
    assert all(call[0] == "assistx-api" and call[1] == 8000 for call in calls)
    assert _TOKEN not in json.dumps(report)
    assert _MODEL not in json.dumps(report)


@pytest.mark.parametrize("unapproved_origin", [
    "https://assistx-api:8000",
    "http://assistx-api:8000/",
    "http://assistx-api:8000/path",
    "http://assistx-api:8000?target=x",
    "http://public.example:8000",
    "http://host.docker.internal:8000",
    "http://assistx-api:8000@attacker.invalid",
    "http://assistx-api:8000#frag",
    "http://assistx-api:BAD",
    "http://assistx-api:0",
    "http://localhost:8000",
    "http://127.0.0.1:8088",
    "",
])
def test_unapproved_origin_fails_before_any_network(monkeypatch, unapproved_origin):
    monkeypatch.setattr(
        probe, "_request", lambda *args, **kw:
        pytest.fail("network must not be reached"),
    )
    report = probe.probe(origin=unapproved_origin, token=_TOKEN, model=_MODEL)
    assert not report["observation_complete"]
    assert report["error_code"] == "origin_not_explicitly_isolated"
    assert report["gate"] == "HOLD"


@pytest.mark.parametrize("bad_stream", [
    b"data: [DONE]\n\n",
    b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
    b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    b'data: [DONE]\n\ndata: [DONE]\n\n',
    b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    b'data: [DONE]\n\nextra',
    b"not an SSE response",
])
def test_truncated_empty_or_replayed_sse_is_never_completion(bad_stream):
    with pytest.raises(probe.CanaryDenied):
        probe._parsed_stream(bad_stream)


@pytest.mark.parametrize("invalid", ["weak", "", "\u2603", "x" * 300])
def test_invalid_client_token_rejected_without_network(monkeypatch, invalid):
    monkeypatch.setattr(
        probe, "_request", lambda *args, **kw:
        pytest.fail("network must not be reached"),
    )
    report = probe.probe(
        origin="http://assistx-api:8000", token=invalid, model=_MODEL,
    )
    assert report["error_code"] == "canary_identity_or_model_invalid"


def test_negative_auth_failure_stops_staging_admission(monkeypatch):
    def fake(_host, _port, *, token, model, stream=True):
        if token == _TOKEN:
            return (200, "text/event-stream", _BODY)
        return (200, "text/event-stream", _BODY)  # Vulnerable backend.
    monkeypatch.setattr(probe, "_request", fake)
    report = probe.probe(
        origin="http://127.0.0.1:8000", token=_TOKEN, model=_MODEL,
    )
    assert report["model_text_and_done_verified"]
    assert not report["invalid_client_token_denied"]
    assert not report["observation_complete"]
    assert report["error_code"] == "unauthorized_client_not_denied"


def test_negative_model_failure_stops_staging_admission(monkeypatch):
    def fake(_host, _port, *, token, model, stream=True):
        if token != _TOKEN:
            return (401, "application/json", b"{}")
        return (200, "text/event-stream", _BODY)
    monkeypatch.setattr(probe, "_request", fake)
    report = probe.probe(
        origin="http://assistx-api:8000", token=_TOKEN, model=_MODEL,
    )
    assert report["invalid_client_token_denied"]
    assert not report["unapproved_model_denied"]
    assert report["error_code"] == "unapproved_model_not_denied"


def test_explicit_cli_optin_is_required(monkeypatch, capsys):
    monkeypatch.setenv("ASSISTX_HERMES_RELAY_STAGING_ORIGIN", "")
    monkeypatch.delenv("ASSISTX_HERMES_RELAY_CLIENT_TOKEN", raising=False)
    monkeypatch.delenv("ASSISTX_HERMES_RELAY_STAGING_MODEL", raising=False)
    with pytest.raises(SystemExit) as outcome:
        probe.main([])
    assert outcome.value.code != 0
    assert "origin" not in capsys.readouterr().out.lower()
    assert probe.main(["--explicit-isolated-staging-only"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["gate"] == "HOLD"
