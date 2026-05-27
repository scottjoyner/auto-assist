import pytest

from assistx import draft_model


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_generate_draft_requires_explicit_configuration(monkeypatch):
    monkeypatch.delenv("DRAFT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("DRAFT_MODEL_NAME", raising=False)

    with pytest.raises(draft_model.DraftModelUnavailable, match="not configured"):
        draft_model.generate_draft("Draft a status note.")


def test_generate_draft_uses_dedicated_endpoint(monkeypatch):
    monkeypatch.setenv("DRAFT_MODEL_BASE_URL", "http://macbook:1234/v1")
    monkeypatch.setenv("DRAFT_MODEL_NAME", "qwen3.5-0.8b")
    monkeypatch.setenv("DRAFT_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("DRAFT_MODEL_TIMEOUT_S", "7")
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        return _Response({"choices": [{"message": {"content": "A concise draft."}}]})

    monkeypatch.setattr(draft_model.requests, "post", fake_post)
    result = draft_model.generate_draft("Draft a status note.", max_tokens=42)

    assert result == {
        "text": "A concise draft.",
        "model": "qwen3.5-0.8b",
        "source": "configured_draft_endpoint",
    }
    assert calls[0][0] == "http://macbook:1234/v1/chat/completions"
    assert calls[0][1]["max_tokens"] == 42
    assert calls[0][2]["Authorization"] == "Bearer test-key"
    assert calls[0][3] == 7
