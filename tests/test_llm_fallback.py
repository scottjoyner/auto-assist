import pytest

from assistx.agents import llm


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"message": {"content": "ok"}}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def test_chat_falls_back_when_primary_fails(monkeypatch):
    monkeypatch.setattr(llm, "FALLBACK_MODELS", ["fallback-a"])
    llm._CB_STATE.clear()

    calls = []

    def fake_post(url, json, timeout):
        calls.append(json["model"])
        if json["model"] == "primary":
            raise RuntimeError("primary down")
        return _Resp(payload={"message": {"content": "from-fallback"}})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    out = llm.chat([{"role": "user", "content": "hi"}], model="primary")
    assert out == "from-fallback"
    assert calls == ["primary", "fallback-a"]


def test_circuit_breaker_opens_after_threshold(monkeypatch):
    monkeypatch.setattr(llm, "CB_FAIL_THRESHOLD", 2)
    monkeypatch.setattr(llm, "CB_OPEN_S", 60)
    monkeypatch.setattr(llm, "FALLBACK_MODELS", [])
    llm._CB_STATE.clear()

    def always_fail(url, json, timeout):
        raise RuntimeError("down")

    monkeypatch.setattr(llm.requests, "post", always_fail)
    with pytest.raises(RuntimeError):
        llm.chat([{"role": "user", "content": "hi"}], model="m1")
    with pytest.raises(RuntimeError):
        llm.chat([{"role": "user", "content": "hi"}], model="m1")

    assert llm._CB_STATE["m1"]["open_until"] > 0
