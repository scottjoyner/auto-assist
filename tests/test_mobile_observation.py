"""Mobile observation is read-only and strictly separate from admission."""
import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from assistx import mobile_agent_routes as mobile
from assistx.mobile_observation import load_observations, project_observations


def witness(captured=None):
    return {"schema_version": 1, "authority": "observational_only_not_runtime_admission",
            "captured_at": (captured or datetime.now(UTC)).isoformat(),
            "source_inventory_sha256": "a" * 64,
            "observations": [
                {"node": "x1-370", "ip": "100.64.43.123", "port": 1234,
                 "state": "resident_verified", "models": [
                     {"id": "/home/private/models/k2.gguf", "state": "resident_verified"},
                     {"id": "not-loaded", "state": "installed_not_loaded"}]},
                {"node": "xwing", "ip": "100.108.99.47", "port": 1235,
                 "state": "advertised_unverified", "models": [
                     {"id": "K2.gguf", "state": "advertised_unverified"}]}]}


def app():
    instance = FastAPI()
    router = APIRouter()
    def auth(request: Request, credentials=None):
        if not request.headers.get("Tailscale-User-Login"):
            raise HTTPException(status_code=401)
        return request.headers["Tailscale-User-Login"]
    mobile.register_mobile_agent_routes(router, auth)
    instance.include_router(router)
    return instance
def test_redacts_all_physical_routes_and_never_marks_selectable():
    result = project_observations(witness())
    assert result["state"] == "fresh"
    assert result["observed_nodes"] == 2
    assert result["observed_endpoints"] == 2
    assert result["resident_endpoints"] == 1
    assert [m["display_name"] for m in result["models"]] == ["k2.gguf", "not-loaded"]
    assert result["models"][0]["observed_instances"] == 2
    assert result["models"][0]["state"] == "resident_verified"
    assert all(m["admitted"] is False and m["selectable"] is False for m in result["models"])
    response = json.dumps(result)
    for sensitive in ("100.64.", "100.108.", "xwing", "x1-370", "/home/", "1234", "1235"):
        assert sensitive not in response


def test_observations_never_echo_untrusted_private_routes_or_invalid_receipts():
    raw = witness()
    raw["source_inventory_sha256"] = "100.64.43.123 private-tailnet-host"
    assert project_observations(raw)["state"] == "invalid"

    raw = witness()
    raw["observations"][0]["models"] += [
        {"id": "http://internal:8088/v1/models", "state": "resident_verified"},
        {"id": "100.64.43.123:1234", "state": "advertised_unverified"},
        {"id": "login@xwing", "state": "resident_verified"},
        {"id": "bad\nnewline", "state": "resident_verified"},
    ]
    result = project_observations(raw)
    assert result["state"] == "fresh"
    assert [m["display_name"] for m in result["models"]] == ["k2.gguf", "not-loaded"]
    assert "100.64." not in json.dumps(result)
    assert "login@" not in json.dumps(result)


def test_stale_and_future_observations_return_no_models():
    now = datetime.now(UTC)
    assert project_observations(witness(now - timedelta(minutes=5)), now=now)["state"] == "stale"
    result = project_observations(witness(now + timedelta(minutes=5)), now=now)
    assert result["state"] == "stale" and result["models"] == []


def test_invalid_authority_and_missing_file_do_not_add_models(tmp_path):
    raw = witness()
    raw["authority"] = "admitted"
    assert project_observations(raw)["state"] == "invalid"
    assert load_observations(None)["state"] == "not_configured"
    assert load_observations(str(tmp_path / "missing.json"))["state"] == "unavailable"
def test_mobile_endpoint_enforces_auth_and_redacts_while_fresh(monkeypatch, tmp_path):
    file = tmp_path / "snapshot.json"
    file.write_text(json.dumps(witness()), encoding="utf-8")
    monkeypatch.setenv("ASSISTX_TAILNET_MODEL_OBSERVATION_FILE", str(file))
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    client = TestClient(app())
    assert client.get("/api/v1/runtime/observations").status_code == 401
    response = client.get("/api/v1/runtime/observations",
                          headers={"Tailscale-User-Login": "scott@example.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "fresh"
    assert len(data["observation_sha256"]) == 64
    assert data["models"] and all(not m["selectable"] for m in data["models"])
    assert "100.64." not in response.text and "/home/" not in response.text
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "someoneelse@example.com")
    assert client.get("/api/v1/runtime/observations",
                      headers={"Tailscale-User-Login": "scott@example.com"}).status_code == 403


def test_no_stale_observation_leak_through_mobile_route(monkeypatch, tmp_path):
    file = tmp_path / "snapshot.json"
    file.write_text(json.dumps(witness(datetime.now(UTC) - timedelta(days=1))))
    monkeypatch.setenv("ASSISTX_TAILNET_MODEL_OBSERVATION_FILE", str(file))
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    response = TestClient(app()).get("/api/v1/runtime/observations",
                                     headers={"Tailscale-User-Login": "scott@example.com"})
    assert response.status_code == 200
    assert response.json()["state"] == "stale"
    assert response.json()["models"] == []