"""Active Control Room read-only telemetry and observed-health contract.

The retired dashboard asserts fabricated per-component scores and time-series
history. The live Control Room uses real observed dependency/runtime/perf data.
No production graph, API service, or external probes run in this test module.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from assistx import control_room, control_room_runtime

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "templates/base.html").read_text()
FRONTEND = (ROOT / "static/js/control_room.js").read_text()


@pytest.fixture()
def observations(monkeypatch):
    # The API installs a snapshot-cache wrapper and older unit tests
    # deliberately seed that global cache with synthetic values. A contract
    # test must never interpret another test's cached value as observed data.
    monkeypatch.setattr(control_room_runtime, "_CACHE_VALUE", None)
    monkeypatch.setattr(control_room_runtime, "_CACHE_EXPIRES_AT", 0.0)
    monkeypatch.setattr(
        control_room_runtime, "collect_router_telemetry",
        lambda _factory: {
            "activity": [], "performance": [], "runtime_samples": [],
            "event_count": 0,
        },
    )
    data = {
        "dependencies": [],
        "runtimes": [],
        "activity": [],
        "performance": [],
        "power": {},
        "doctor": {},
    }
    monkeypatch.setattr(
        control_room, "collect_dependencies",
        lambda _factory: (data["dependencies"], {}),
    )
    monkeypatch.setattr(
        control_room, "collect_runtimes",
        lambda _factory, _admission: data["runtimes"],
    )
    monkeypatch.setattr(
        control_room, "collect_activity",
        lambda _factory: data["activity"],
    )
    monkeypatch.setattr(
        control_room, "collect_performance",
        lambda _factory: data["performance"],
    )
    monkeypatch.setattr(control_room, "collect_fleet_nodes", lambda _factory: [])
    monkeypatch.setattr(control_room, "collect_power", lambda: data["power"])
    monkeypatch.setattr(
        control_room, "_load_doctor_findings", lambda: data["doctor"]
    )
    monkeypatch.setattr(
        control_room, "island_recovery_snapshot", lambda: {}
    )
    return data


def snapshot():
    return control_room.build_overview(lambda: None)


def isolated_control_room():
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
    templates = Jinja2Templates(directory=str(ROOT / "templates"))
    app.include_router(
        control_room.build_control_room_router(
            lambda: None, lambda: "synthetic-reader", templates,
        )
    )
    return TestClient(app)


def test_missing_required_observations_is_unknown_not_healthy(observations):
    state = snapshot()
    assert state["overall_status"] == "unknown"
    assert state["summary"]["required_dependency_failures"] == 0
    assert state["summary"]["average_tokens_per_second"] is None
    assert state["summary"]["error_percent"] is None
    assert state["runtimes"] == []
    assert state["performance"] == []


def test_required_failed_dependency_marks_degraded(observations):
    observations["dependencies"] = [
        {"name": "Neo4j", "status": "failed", "required": True},
        {"name": "Dashboard", "status": "healthy", "required": False},
    ]
    state = snapshot()
    assert state["overall_status"] == "degraded"
    assert state["summary"]["required_dependency_failures"] == 1
    assert state["dependencies"][0]["name"] == "Neo4j"


def test_required_healthy_dependency_comes_from_observed_probe(observations):
    observations["dependencies"] = [
        {"name": "Neo4j", "status": "healthy", "required": True}
    ]
    state = snapshot()
    assert state["overall_status"] == "healthy"
    assert state["summary"]["required_dependency_failures"] == 0


def test_optional_only_probes_never_imply_full_health(observations):
    observations["dependencies"] = [
        {"name": "Optional service", "status": "healthy", "required": False},
    ]
    assert snapshot()["overall_status"] == "unknown"


def test_runtime_summary_uses_actual_active_queued_slots(observations):
    observations["runtimes"] = [
        {
            "status": "online", "active": 2, "queued": 3,
            "parallel_slots": 3, "loaded_models": ["model-a"],
        },
        {
            "status": "offline", "active": 1, "queued": 1,
            "parallel_slots": 0, "loaded_models": [],
        },
    ]
    summary = snapshot()["summary"]
    assert summary["runtime_count"] == 2
    assert summary["healthy_runtime_count"] == 1
    assert summary["active_requests"] == 3
    assert summary["queued_requests"] == 4
    assert summary["available_slots"] == 0
    assert summary["loaded_model_count"] == 1


def test_weighted_performance_from_actual_runs_not_synthetic_score(observations):
    observations["performance"] = [
        {"runs": 2, "tps_avg": 10.0, "error_percent": 0.0},
        {"runs": 1, "tps_avg": 4.0, "error_percent": 30.0},
    ]
    summary = snapshot()["summary"]
    assert summary["average_tokens_per_second"] == 8.0
    assert summary["error_percent"] == 10.0


def test_no_computed_historical_trend_or_export_claim_in_raw_overview(observations):
    state = snapshot()
    assert "trend_data" not in state
    assert "component_health" not in state
    assert "export_available" not in state
    assert "health_score" not in state
    assert "critical_alerts" not in state
    assert "collected_at_ts" in state


def test_control_room_route_serves_health_and_trend_panels():
    with isolated_control_room() as client:
        r = client.get("/control-room")
    assert r.status_code == 200
    assert 'id="component-health"' in r.text
    assert 'class="component-health"' in r.text
    assert 'id="trend-data"' in r.text
    assert 'class="trend-data"' in r.text


def test_control_room_route_exposes_manual_export_with_internal_data_warning():
    with isolated_control_room() as client:
        r = client.get("/control-room")
    assert r.status_code == 200
    assert 'id="export-data"' in r.text
    assert "EXPORT DATA (INTERNAL JSON)" in r.text
    assert "Download includes internal fleet identifiers" in r.text
    assert 'id="quick-actions"' in r.text


def test_health_status_uses_unknown_for_unobserved_snapshot():
    assert "if (!hasDoctor" not in FRONTEND  # fixed spelling
    assert "if (!countsPresent" in FRONTEND
    assert "return null;" in FRONTEND
    assert "healthScoreElement.textContent = state.healthScore == null" in FRONTEND
    assert '"overall_status": (' in (
        ROOT / "src/assistx/control_room.py"
    ).read_text()


def test_component_text_is_escaped_before_html_injection():
    for value in (
        "${esc(comp.name)}", "${esc(comp.issues)}",
        "${esc(comp.category)}", "${esc(comp.runtime)}",
        "${esc(trend.metric)}", "${esc(trend.change)}",
        "${esc(alert)}",
        "${statusClass(comp.status)}",
    ):
        assert value in FRONTEND


def test_single_snapshot_has_no_fabricated_delta():
    assert "change: '+1'" not in FRONTEND
    assert "trend: 'increasing'" not in FRONTEND
    assert FRONTEND.count("change: 'snapshot only'") >= 4
    assert "time-series trend or change is unknown without history" in TEMPLATE
