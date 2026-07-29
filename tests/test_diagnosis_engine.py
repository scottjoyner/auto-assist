from assistx.diagnosis_engine import diagnose_incident


def test_diagnosis_is_evidence_backed_and_non_mutating():
    incident = {
        "incident_key": "node-a:offline",
        "node_id": "node-a",
        "incident_type": "node_offline",
        "severity": "critical",
        "detail": "service probe failed",
    }
    snapshot = {
        "nodes": [{
            "hostname": "node-a",
            "service_ok": False,
            "report_fresh": True,
            "inflight_tasks": 2,
            "loaded_models": ["model-a"],
        }]
    }

    diagnosis = diagnose_incident(incident, snapshot)

    assert diagnosis["hypothesis"]
    assert diagnosis["bounded_probes"]
    assert diagnosis["recommended_recovery"]["action"] == "restore_service"
    assert diagnosis["recommended_recovery"]["requires_approval"] is True
    assert diagnosis["mutated"] is False


def test_diagnosis_id_is_stable_for_same_evidence():
    incident = {"incident_key": "x", "node_id": "n", "incident_type": "stale_report"}
    snapshot = {"nodes": [{"hostname": "n", "report_fresh": False}]}

    assert (
        diagnose_incident(incident, snapshot)["diagnosis_id"]
        == diagnose_incident(incident, snapshot)["diagnosis_id"]
    )
