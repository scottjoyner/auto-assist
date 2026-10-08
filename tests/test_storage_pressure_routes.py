"""Read-only authenticated storage projection contract tests."""
from __future__ import annotations

import json
import pathlib
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from assistx.storage_pressure_routes import (
    build_storage_pressure_router, project_storage_report)

def fixture(now: float) -> dict:
    return {"schema":"assistx-fleet-storage-pressure/v1",
            "observed_epoch":int(now),"observation_ok":True,
            "ssd_severity":"warning","ssd_used_percent":95.29,
            "ssd_available_bytes":185445707776,
            "nas_available_bytes":46508539715584,
            "nas_used_percent":47.18,
            "nas_l5_admission":"HOLD",
            "nas_btrfs_error_count":0,
            "root_census_complete":False,
            "published_nas_manifest_bytes":1892909329,
            "source_release_blocked":True,
            "automatic_cleanup_allowed":False,"reclaimed_bytes":0,
            "internal_path":"/private/hide-this",
            "secret":"never-serve-this-token"}

def client_for(path: pathlib.Path):
    app=FastAPI()
    def auth(x_test_auth: str | None=Header(default=None)):
        if x_test_auth!="allowed":
            raise HTTPException(status_code=401)
        return "tester"
    app.include_router(build_storage_pressure_router(auth,report_path=path))
    return TestClient(app)

def test_storage_api_requires_auth_and_only_safe_fields(tmp_path):
    path=tmp_path/"report.json"
    path.write_text(json.dumps(fixture(time.time())))
    client=client_for(path)
    assert client.get("/api/fleet/storage-pressure").status_code == 401
    response=client.get("/api/fleet/storage-pressure",
                        headers={"x-test-auth":"allowed"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    result=response.json()
    assert result["state"]=="fresh"
    assert result["ssd_used_percent"]==95.29
    assert result["nas_l5_admission"]=="HOLD"
    assert result["nas_new_background_copy_permitted"] is False
    assert result["automatic_cleanup_allowed"] is False
    assert "secret" not in response.text
    assert "/private/hide-this" not in response.text

def test_stale_report_disallows_new_writer_and_hides_utilization(tmp_path):
    path=tmp_path/"report.json"
    path.write_text(json.dumps(dict(fixture(time.time()-3600),
                                    nas_l5_admission="GO")))
    data=project_storage_report(path)
    assert data["state"]=="stale"
    assert data["nas_new_background_copy_permitted"] is False
    assert data["ssd_available_bytes"] is None
    assert data["nas_l5_admission"]=="UNKNOWN"

def test_missing_invalid_too_large_fail_closed(tmp_path):
    path=tmp_path/"report.json"
    assert project_storage_report(path)["state"]=="unavailable"
    path.write_text("{invalid")
    assert project_storage_report(path)["reason"]=="snapshot_unreadable"
    path.write_text("x"*9000)
    assert project_storage_report(path)["reason"]=="snapshot_too_large"
    path.write_text(json.dumps(dict(fixture(time.time()),schema="other")))
    assert project_storage_report(path)["reason"]=="schema_mismatch"
    path.write_text(json.dumps(dict(fixture(time.time()),
                                    observed_epoch=time.time()+700)))
    assert project_storage_report(path)["reason"]=="invalid_timestamp"

def test_missing_capacity_when_ok_claimed_fails_closed(tmp_path):
    path=tmp_path/"report.json"
    row=dict(fixture(time.time()), nas_l5_admission="GO")
    row.pop("ssd_available_bytes")
    path.write_text(json.dumps(row))
    d=project_storage_report(path)
    assert d["state"] == "degraded"
    assert d["nas_new_background_copy_permitted"] is False
    assert d["ssd_available_bytes"] is None

def test_report_never_turns_receipt_into_release_authority(tmp_path):
    path=tmp_path/"report.json"
    path.write_text(json.dumps(dict(fixture(time.time()),
                      nas_l5_admission="GO",automatic_cleanup_allowed=True,
                      source_release_blocked=False,reclaimed_bytes=1000)))
    result=project_storage_report(path)
    assert result["nas_l5_observed_go"] is True
    assert result["nas_new_background_copy_permitted"] is False
    assert result["source_release_blocked"] is True
    assert result["automatic_cleanup_allowed"] is False
    assert result["reclaimed_bytes"]==0


def test_fleet_dashboard_has_read_only_storage_card():
    root = pathlib.Path(__file__).resolve().parents[1]
    template = (root / "templates/fleet_dashboard.html").read_text()
    assert 'id="section-storage-pressure"' in template
    assert 'id="storage-ssd-used"' in template
    assert 'id="storage-admission"' in template
    assert "fetch('/api/fleet/storage-pressure'" in template
    assert "refusing stale admission or capacity readings" in template
    assert "Source-release BLOCKED; automatic cleanup disabled." in template
