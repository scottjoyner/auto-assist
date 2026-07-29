from assistx.diagnostic_probes import execute_diagnostic_probes


def test_declared_probes_execute_and_replace_estimated_confidence():
    diagnosis = {
        "node_id": "node-a",
        "model_id": "model-a",
        "incident_type": "high_latency",
        "bounded_probes": ["service_probe", "inventory_probe", "capacity_probe", "canary_inference"],
        "confidence": 0.2,
    }
    snapshot = {
        "nodes": [{
            "hostname": "node-a",
            "report_fresh": True,
            "loaded_models": ["model-a"],
            "inflight_tasks": 0,
            "max_concurrent": 2,
        }]
    }

    result = execute_diagnostic_probes(
        diagnosis,
        snapshot,
        service_probe=lambda _: {"ok": True, "response_ms": 4},
        canary_probe=lambda _node, model: {"ok": model == "model-a", "latency_ms": 10},
    )

    assert result["evidence_status"] == "measured"
    assert result["probe_summary"] == {
        "requested": 4,
        "completed": 4,
        "successful": 4,
        "deadline_seconds": 20.0,
    }
    assert result["confidence"] > diagnosis["confidence"]
    assert result["mutated"] is False


def test_unavailable_probe_adapter_lowers_evidence_coverage():
    result = execute_diagnostic_probes(
        {"node_id": "node-a", "bounded_probes": ["unknown_probe"]},
        {"nodes": [{"hostname": "node-a"}]},
        service_probe=lambda _: {"ok": True},
    )

    assert result["probe_summary"]["completed"] == 0
    assert result["confidence"] < 0.5
