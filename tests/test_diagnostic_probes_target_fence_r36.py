"""Hermetic fail-closed contract for diagnosis probe evidence (round 36).

A diagnosis may only be labelled ``measured`` when a real, identified target
node from the snapshot was actually probed.  No live service, socket, DB or
network mount is used here; every probe is an in-process stub.
"""

from assistx.diagnostic_probes import execute_diagnostic_probes


def _run(diagnosis, snapshot, service_calls):
    def service_probe(node):
        service_calls.append(node)
        return {"ok": True, "response_ms": 1}

    return execute_diagnostic_probes(
        diagnosis,
        snapshot,
        service_probe=service_probe,
        canary_probe=lambda _node, _model: {"ok": True},
    )


def _assert_no_evidence(result, expected_probes):
    assert result["evidence_status"] == "target_node_missing"
    assert result["confidence"] == 0.0
    assert result["probe_summary"]["completed"] == 0
    assert result["probe_summary"]["successful"] == 0
    assert result["probe_summary"]["requested"] == expected_probes
    assert len(result["probe_results"]) == expected_probes
    assert all(row["ok"] is False for row in result["probe_results"])
    assert all(row["reason"] == "target_node_missing" for row in result["probe_results"])
    assert result["mutated"] is False


def test_missing_target_node_cannot_yield_measured_evidence():
    calls = []
    diagnosis = {
        "node_id": "ghost-node",
        "incident_type": "high_latency",
        "bounded_probes": ["service_probe", "capacity_probe", "recent_failures"],
        "confidence": 0.9,
    }
    snapshot = {
        "nodes": [{
            "hostname": "real-node",
            "report_fresh": True,
            "inflight_tasks": 0,
            "max_concurrent": 2,
        }]
    }

    result = _run(diagnosis, snapshot, calls)

    _assert_no_evidence(result, 3)
    assert calls == []  # never probe a fabricated node


def test_blank_target_id_cannot_match_an_anonymous_snapshot_row():
    calls = []
    diagnosis = {
        "bounded_probes": ["service_probe", "capacity_probe"],
        "confidence": 0.9,
    }
    snapshot = {"nodes": [{"ip": "10.0.0.1"}]}  # row has no hostname/id

    result = _run(diagnosis, snapshot, calls)

    _assert_no_evidence(result, 2)
    assert calls == []


def test_no_completed_probe_is_not_labelled_measured():
    diagnosis = {"node_id": "real-node", "bounded_probes": ["unknown_probe"]}
    snapshot = {"nodes": [{"hostname": "real-node"}]}

    result = _run(diagnosis, snapshot, [])

    assert result["evidence_status"] == "unavailable"
    assert result["probe_summary"]["completed"] == 0
    assert result["probe_summary"]["successful"] == 0


def test_identified_target_still_produces_measured_evidence():
    calls = []
    diagnosis = {
        "node_id": "real-node",
        "incident_type": "high_latency",
        "bounded_probes": ["service_probe", "capacity_probe"],
        "confidence": 0.2,
    }
    snapshot = {
        "nodes": [{
            "hostname": "real-node",
            "inflight_tasks": 0,
            "max_concurrent": 2,
        }]
    }

    result = _run(diagnosis, snapshot, calls)

    assert result["evidence_status"] == "measured"
    assert result["probe_summary"]["completed"] == 2
    assert result["probe_summary"]["successful"] == 2
    assert result["confidence"] > diagnosis["confidence"]
    assert len(calls) == 1
    assert result["mutated"] is False
