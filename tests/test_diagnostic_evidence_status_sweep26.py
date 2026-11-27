"""Sweep26: ``evidence_status`` must not claim a measurement when none occurred.

``execute_diagnostic_probes`` stamps every returned diagnosis with
``evidence_status="measured"`` unconditionally. A malformed or incomplete
diagnosis whose declared probes never produced a result (empty allowlist, or
only probes with no adapter) is therefore reported as measured evidence even
though ``probe_summary.completed`` is zero. Downstream consumers that trust the
status label are silently misled. These fixtures are pure in-process calls with
synthetic dicts; no socket, DB, container, credential or live node is touched.
"""

from assistx.diagnostic_probes import execute_diagnostic_probes

_NODE = {
    "hostname": "node-a",
    "report_fresh": True,
    "inflight_tasks": 0,
    "max_concurrent": 2,
}
_SNAPSHOT = {"nodes": [_NODE]}


def _service_probe(_node):
    return {"ok": True, "response_ms": 4}


def test_empty_probe_allowlist_is_not_reported_as_measured():
    """No probes declared -> nothing was measured -> status must not say measured."""
    result = execute_diagnostic_probes(
        {"node_id": "node-a", "incident_type": "high_latency", "bounded_probes": []},
        _SNAPSHOT,
        service_probe=_service_probe,
    )
    assert result["probe_summary"]["completed"] == 0
    assert result["evidence_status"] == "unmeasured"


def test_only_unavailable_adapters_are_not_reported_as_measured():
    """Every declared probe lacked an adapter -> no measurement was taken."""
    result = execute_diagnostic_probes(
        {"node_id": "node-a", "bounded_probes": ["canary_inference", "unknown_probe"]},
        _SNAPSHOT,
        service_probe=_service_probe,
    )
    assert result["probe_summary"]["completed"] == 0
    assert result["evidence_status"] == "unmeasured"


def test_real_probe_is_reported_as_measured():
    """Control (passes on base): a probe that produced a result is a measurement."""
    result = execute_diagnostic_probes(
        {"node_id": "node-a", "bounded_probes": ["service_probe"]},
        _SNAPSHOT,
        service_probe=_service_probe,
    )
    assert result["probe_summary"]["completed"] == 1
    assert result["evidence_status"] == "measured"


def test_unsuccessful_measurement_still_counts_as_measured():
    """Control (passes on base): a probe that ran and found a bad state still measured."""
    result = execute_diagnostic_probes(
        {"node_id": "node-a", "bounded_probes": ["agent_heartbeat"]},
        {"nodes": [{"hostname": "node-a", "last_seen_ago_sec": 999}]},
        service_probe=_service_probe,
    )
    assert result["probe_results"][0]["ok"] is False
    assert result["probe_summary"]["completed"] == 1
    assert result["evidence_status"] == "measured"
