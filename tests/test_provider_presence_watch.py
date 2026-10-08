"""Provider presence checks never use inference or paid fallback."""
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/provider_presence_watch.py"
spec = importlib.util.spec_from_file_location("provider_presence_watch", SCRIPT)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def test_probe_skip_for_missing_credentials(monkeypatch):
    provider = next(p for p in watch.PROVIDERS if p["id"] == "grok_xai_api")
    def must_not_call(*args, **kwargs):
        raise AssertionError("should not request unconfigured credentialed provider")
    monkeypatch.setattr(watch, "build_opener", must_not_call)
    result = watch._probe_http(provider, {})
    assert result["status"] == "credential_missing"


def test_one_endpoint_per_provider_and_no_inference(tmp_path):
    requests = []
    def fake_http(provider, env):
        requests.append(provider["id"])
        return {"status": "catalog_reachable", "http_code": 200, "delay": 900}
    def fake_binary(provider, env):
        requests.append(provider["id"])
        return {"status": "cli_ready", "delay": 900}
    path = tmp_path / "presence.json"
    a = watch.run(path, now=1000, env={}, http_probe=fake_http, binary_probe=fake_binary)
    assert len(requests) == len(watch.PROVIDERS) == len(set(requests))
    assert a["inference_probe_requests"] == 0
    assert a["auto_generated_tokens"] == 0
    assert all(not v["inference_qualified"] for v in a["providers"].values())
    b = watch.run(path, now=1050, env={}, http_probe=fake_http, binary_probe=fake_binary)
    assert len(requests) == len(watch.PROVIDERS)
    assert b["providers"]["openrouter"]["last_reachable_epoch"] == 1000


def test_provider_backoff_remains_independent(tmp_path):
    events = []
    def fake_http(p, env):
        events.append(p["id"])
        if p["id"] == "kilo_anonymous":
            return {"status": "rate_limited", "http_code": 429, "delay": 3600}
        return {"status": "catalog_reachable", "http_code": 200, "delay": 120}
    paths = [p for p in watch.PROVIDERS if p["id"] in ("kilo_anonymous", "openrouter")]
    f = tmp_path / "state.json"
    a = watch.run(f, providers=paths, now=1000, env={}, http_probe=fake_http)
    assert a["providers"]["kilo_anonymous"]["next_due_epoch"] == 4600
    assert a["providers"]["openrouter"]["next_due_epoch"] == 1120
    watch.run(f, providers=paths, now=1200, env={}, http_probe=fake_http)
    assert events.count("kilo_anonymous") == 1
    assert events.count("openrouter") == 2


def test_state_private_and_no_secrets(tmp_path):
    secret = "secret-do-not-persist"
    p = [{"id": "cohere", "kind": "http", "url": "https://api.cohere.com/v1/models",
          "env": "COHERE_API_KEY", "auth_required": True, "min_interval": 1800}]
    state = tmp_path / "presence.json"
    watch.run(state, providers=p, now=100, env={"COHERE_API_KEY": secret},
              http_probe=lambda p, env: {"status": "catalog_reachable", "delay": 100})
    assert secret not in state.read_text()
    assert state.stat().st_mode & 0o077 == 0
    assert json.loads(state.read_text())["providers"]["cohere"]["status"] == "catalog_reachable"
