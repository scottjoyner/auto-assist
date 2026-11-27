"""Hermetic contract tests: dashboard trend severity must mirror component status.

Loaded directly from source so no AssistX package import, service, socket,
container or database is involved. Synthetic in-memory node dicts only.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "assistx" / "dashboard_readonly.py"


def _load_derive():
    spec = importlib.util.spec_from_file_location("sweep40x_dashboard_readonly", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.derive_dashboard_health


derive_dashboard_health = _load_derive()


def _fresh_nodes(*service_ok: bool) -> list[dict[str, Any]]:
    return [
        {"hostname": f"node-{index}", "report_fresh": True, "service_ok": ok} for index, ok in enumerate(service_ok)
    ]


def test_partially_unavailable_fleet_is_not_reported_stable() -> None:
    out = derive_dashboard_health(_fresh_nodes(True, False))
    component = out["component_health"][0]
    trend = out["trend_data"][0]

    assert component["status"] == "degraded"
    assert component["issues"] == ["1 fresh reported node(s) unavailable"]
    assert trend["severity"] == "warning"


@pytest.mark.parametrize(
    "nodes,expected_status,expected_severity",
    [
        ([], "unknown", "warning"),
        (_fresh_nodes(False, False), "critical", "critical"),
        (_fresh_nodes(True, False), "degraded", "warning"),
        (_fresh_nodes(True, True), "healthy", "stable"),
    ],
)
def test_trend_severity_mirrors_component_status(
    nodes: list[dict[str, Any]], expected_status: str, expected_severity: str
) -> None:
    out = derive_dashboard_health(nodes)
    assert out["component_health"][0]["status"] == expected_status
    assert out["trend_data"][0]["severity"] == expected_severity


def test_majority_offline_is_warning_not_stable() -> None:
    out = derive_dashboard_health(_fresh_nodes(False, False, True))
    assert out["component_health"][0]["status"] == "degraded"
    assert out["trend_data"][0]["current"] == 1
    assert out["trend_data"][0]["severity"] == "warning"


def test_unverified_nodes_do_not_downgrade_all_fresh_online() -> None:
    out = derive_dashboard_health(
        [
            {"report_fresh": True, "service_ok": True},
            {"report_fresh": False, "service_ok": True},
            {"report_fresh": True, "service_ok": "unknown"},
        ]
    )
    assert out["component_health"][0]["status"] == "healthy"
    assert out["trend_data"][0]["severity"] == "stable"
