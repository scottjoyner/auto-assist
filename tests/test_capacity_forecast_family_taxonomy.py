"""Capacity forecast must recognize every family the task router can emit.

``task_family_routing.infer_task_family`` treats ``compression`` as a first-class
family (with its own virtual model).  The forecast's family classifier previously
omitted it, so a READY compression task was silently reported under ``general``
and family-level demand was understated.  These tests use only synthetic
in-memory fixtures — no services, sockets, or filesystem state.
"""

from __future__ import annotations

from assistx.capacity_forecast import build_capacity_forecast, task_family
from assistx.task_family_routing import infer_task_family


def test_forecast_family_classifier_recognizes_compression() -> None:
    assert task_family({"payload": {"task_family": "compression"}}) == "compression"


def test_forecast_buckets_router_emitted_compression_family() -> None:
    # The family under test is whatever the canonical router emits for an
    # explicit compression workload — this ties the two taxonomies together.
    family = infer_task_family(payload={"task_family": "compression"})
    assert family == "compression"

    tasks = [
        {"id": "task-1", "status": "READY", "payload": {"task_family": family}},
        {"id": "task-2", "status": "READY", "payload": {"task_family": "coding"}},
    ]
    result = build_capacity_forecast(tasks, [], {})
    by_family = result["ready_by_task_family"]

    assert by_family.get("compression") == 1
    assert by_family.get("coding") == 1
    assert "general" not in by_family
