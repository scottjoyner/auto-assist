"""Read-only AssistX provider-burn API and authorization contract."""
from __future__ import annotations

import json
import pathlib
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from assistx.provider_burn_routes import build_provider_burn_router, project_burn_report

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sample_report(stamp: float):
    return {"schema": "free-endpoint-burn-dashboard/v1",
            "generated_epoch": stamp, "complete": True, "step_events": 42,
            "coverage": [{"source": "/private/secret.db", "complete": True}],
            "by_provider": [{"provider": "kilo", "presence": "catalog_reachable",
                             "observed_tokens_1h": 10, "observed_tokens_24h": 100,
                             "observed_tokens_7d": 400, "forecast_next_24h": 100,
                             "token_classes_24h": {"cache_read": 55},
                             "secret": "must-never-leak"}],
            "models": [{"provider": "kilo", "model": "<img src=x onerror=alert(1)>",
                        "name_suggests_free": False, "age_minutes": 5,
                        "windows": {"24h": {"total": 100}, "7d": {"total": 400}}}]}


def app_for(path):
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
    def require_auth(x_test_auth: str | None = Header(default=None)):
        if x_test_auth != "allowed":
            raise HTTPException(401)
        return "tester"
    app.include_router(build_provider_burn_router(
        require_auth, Jinja2Templates(directory=str(ROOT / "templates")), report_path=path))
    return TestClient(app)


def test_auth_covers_api_and_page(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(sample_report(time.time())))
    client = app_for(path)
    assert client.get("/api/fleet/provider-burn").status_code == 401
    assert client.get("/provider-usage").status_code == 401
    data = client.get("/api/fleet/provider-burn", headers={"x-test-auth": "allowed"})
    assert data.status_code == 200
    assert data.headers["cache-control"] == "private, no-store"
    assert data.json()["state"] == "fresh"
    assert data.json()["observed_tokens_24h"] == 100
    assert data.json()["quota_remaining"] is None
    assert "secret" not in data.text and "/private/secret.db" not in data.text
    page = client.get("/provider-usage", headers={"x-test-auth": "allowed"})
    assert page.status_code == 200
    assert "Provider usage &amp; burn" in page.text or "Provider usage & burn" in page.text


def test_missing_bad_and_stale_report(tmp_path):
    path = tmp_path / "report.json"
    assert project_burn_report(path)["state"] == "unavailable"
    path.write_text("{invalid")
    assert project_burn_report(path)["reason"] == "snapshot_unreadable"
    stamp = time.time() - 8000
    path.write_text(json.dumps(sample_report(stamp)))
    data = project_burn_report(path)
    assert data["state"] == "stale"
    assert data["observed_tokens_24h"] == 100
    assert data["free_entitlement_verified"] is False


def test_partial_report_disables_topline_acceptance(tmp_path):
    path = tmp_path / "report.json"
    row = sample_report(time.time())
    row["complete"] = False
    path.write_text(json.dumps(row))
    data = project_burn_report(path)
    assert data["complete"] is False
    assert data["observed_tokens_24h"] is None
    assert data["forecast_next_24h"] is None


def test_malformed_report_is_not_promoted_or_crashing(tmp_path):
    path = tmp_path / "report.json"
    row = sample_report(time.time())
    row["by_provider"] = {}
    path.write_text(json.dumps(row))
    assert project_burn_report(path)["reason"] == "invalid_report_shape"
    row = sample_report(float("nan"))
    path.write_text(json.dumps(row))
    assert project_burn_report(path)["reason"] == "invalid_timestamp"
    row = sample_report(time.time())
    row["by_provider"][0]["token_classes_24h"] = ["malformed"]
    row["models"][0]["windows"] = ["malformed"]
    path.write_text(json.dumps(row))
    report = project_burn_report(path)
    assert report["state"] == "fresh"
    assert report["providers"][0]["cache_read_24h"] is None
    assert report["models"][0]["tokens_7d"] is None
