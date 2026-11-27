"""Hermetic fail-closed tests for non-finite TPS in the capacity forecast (r16).

The value matrix is fetched from the live router's JSON endpoint. Python's
``json.loads`` accepts ``NaN``/``Infinity``, and a producer can also emit the
same words as numeric strings. A non-finite reading is not a measurement: it
must not fabricate a tasks/hour rate, and in the ``+inf`` case it must not
report the ready queue as zero hours with no bottleneck at all.

Pure dicts only: no I/O, no clock, no router, no model load.
"""

from __future__ import annotations

import math

from assistx.capacity_forecast import build_capacity_forecast

TASKS = [{"id": "ready-1", "status": "READY", "kind": "coding"}]
NODES = [{"hostname": "x1", "online": True, "max_concurrent": 2}]


def _forecast(tps):
    matrix = {"entries": [{"loaded": True, "online": True, "tokens_per_second": tps}]}
    return build_capacity_forecast(TASKS, NODES, matrix)


def test_infinite_tps_cannot_fabricate_zero_queue_delay():
    result = _forecast(float("inf"))
    summary = result["summary"]

    assert summary["aggregate_loaded_tps"] == 0
    assert summary["estimated_tasks_per_hour"] == 0
    assert summary["estimated_queue_hours"] is None
    assert "no measured TPS for loaded online models" in result["bottlenecks"]


def test_nan_tps_is_not_a_measurement():
    result = _forecast(float("nan"))
    summary = result["summary"]

    for value in (
        summary["aggregate_loaded_tps"],
        summary["estimated_tasks_per_hour"],
        summary["estimated_queue_hours"],
    ):
        assert value is None or (not isinstance(value, float) or not math.isnan(value))
    assert summary["estimated_tasks_per_hour"] == 0
    assert "no measured TPS for loaded online models" in result["bottlenecks"]


def test_string_inf_is_not_a_measurement():
    result = _forecast("inf")
    assert result["summary"]["estimated_tasks_per_hour"] == 0
    assert result["summary"]["estimated_queue_hours"] is None
    assert "no measured TPS for loaded online models" in result["bottlenecks"]


def test_non_numeric_tps_is_skipped_not_raised():
    result = _forecast("fast")
    assert result["summary"]["estimated_tasks_per_hour"] == 0
    assert "no measured TPS for loaded online models" in result["bottlenecks"]


def test_negative_tps_is_not_a_measurement():
    result = _forecast(-5)
    assert result["summary"]["estimated_tasks_per_hour"] == 0
    assert "no measured TPS for loaded online models" in result["bottlenecks"]


def test_a_bad_row_does_not_cancel_a_finite_measurement():
    matrix = {"entries": [
        {"loaded": True, "online": True, "tokens_per_second": 10},
        {"loaded": True, "online": True, "tokens_per_second": float("inf")},
    ]}
    result = build_capacity_forecast(TASKS, NODES, matrix)

    assert result["summary"]["aggregate_loaded_tps"] == 10
    assert result["summary"]["estimated_tasks_per_hour"] == round(10 * 3600.0 / 2048, 2)
    assert result["summary"]["estimated_queue_hours"] is not None
    assert result["summary"]["estimated_queue_hours"] > 0
    assert result["bottlenecks"] == []
