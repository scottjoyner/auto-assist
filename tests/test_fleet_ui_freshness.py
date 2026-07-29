import assistx.api as api
import assistx.fleet_executor as fleet_executor


def test_dashboard_uses_running_fleet_executor(monkeypatch):
    live = object()
    monkeypatch.setattr(fleet_executor, "get_fleet_executor", lambda: live)
    monkeypatch.setattr(api, "_fleet_executor_instance", None)

    assert api._get_fleet_executor() is live


def test_live_probe_falls_back_to_openai_compatible_models(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, timeout):
        calls.append(url)
        if url.endswith("/api/v1/models"):
            return Response(404, {})
        if url.endswith("/v1/models"):
            return Response(200, {"data": [{"id": "headless"}]})
        raise AssertionError(url)

    monkeypatch.setattr(api.requests, "get", fake_get)

    result = api._live_probe_node("http://100.78.106.121:1234/v1")

    assert result["online"] is True
    assert result["protocol"] == "openai-compatible"
    assert result["models"] == ["headless"]
    assert result["loaded_models"] == ["headless"]
    assert calls == [
        "http://100.78.106.121:1234/api/v1/models",
        "http://100.78.106.121:1234/v1/models",
    ]
