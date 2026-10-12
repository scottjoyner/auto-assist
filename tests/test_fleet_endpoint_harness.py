"""Deterministic no-network contract checks for endpoint fleet deployment."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from assistx.fleet_endpoint_harness import build_report


NOW = 1791650000.0


def inventory(*items):
    return {"schema": "fleet-endpoint-inventory/v1", "endpoints": list(items)}


def endpoint(kind="cloud_chat", model="cohere/north-mini-code:free", **other):
    return {
        "id": "model-a", "kind": kind, "provider": "kilo_free",
        "model": model, "upstream_group": "unknown", **other,
    }


def presence(epoch=NOW):
    return {
        "schema": "provider-presence/v1",
        "providers": {"kilo_anonymous": {"status": "catalog_reachable", "checked_epoch": epoch}},
    }


def report(root, endpoints=None, catalog=None, observations=None):
    return build_report(
        inventory(*(endpoints if endpoints is not None else [endpoint()])),
        presence=catalog,
        local_observations=observations,
        state_root=root,
        now=NOW,
        freshness_seconds=900,
    )


def receipt(root: Path, *, session="ses_fixture", status="completed",
            model="cohere/north-mini-code:free", cost=0.0,
            text=True, stop=True, hash_override=None):
    directory = root / "manifests"
    traces = root / "traces"
    directory.mkdir(exist_ok=True)
    traces.mkdir(exist_ok=True)
    events = []
    if text:
        events.append({"type": "text", "sessionID": session, "part": {"text": "review finished"}})
    if stop:
        events.append({"type": "step_finish", "sessionID": session, "part": {"reason": "stop"}})
    trace = traces / (session + ".jsonl")
    trace.write_text("".join(json.dumps(event) + "\n" for event in events))
    digest = hash_override or hashlib.sha256(trace.read_bytes()).hexdigest()
    manifest = {
        "session_id": session,
        "status": status, "provider": "kilo_free",
        "requested_model": "cohere/north-mini-code:free",
        "effective_model": model, "model_identity_complete": True,
        "cost": cost, "stdout": {"path": str(trace), "sha256": digest},
        "objective": "SECRET_USER_PROMPT", "api_key": "SECRET_TEST_KEY",
    }
    path = directory / (session + ".json")
    path.write_text(json.dumps(manifest))
    os.utime(path, (NOW, NOW))
    return path, trace


def test_catalog_only_never_grants_admission(tmp_path):
    row = report(tmp_path, catalog=presence())["endpoints"][0]
    assert row["catalog_status"] == "fresh_catalog"
    assert row["classification"] == "not_qualified"
    assert row["execution"]["status"] == "no_execution_evidence"
    assert row["production_admitted"] is False
    assert row["automatic_dispatch_allowed"] is False


def test_verified_local_receipt_is_not_a_lease_or_archive(tmp_path):
    receipt(tmp_path)
    outcome = report(tmp_path, catalog=presence())
    row = outcome["endpoints"][0]
    assert row["classification"] == "local_receipt_pass"
    assert row["execution"]["status"] == "local_trace_verified"
    assert row["execution"]["fresh"] is True
    assert row["production_admitted"] is False
    assert row["upstream_group_declared"] is False
    assert row["upstream_group_verified"] is False
    assert row["independent_archive_verified"] is False
    assert "SECRET_" not in json.dumps(outcome)
    assert outcome["inference_requests"] == 0


@pytest.mark.parametrize("changes,expected", [
    ({"status": "failed"}, "run_not_completed"),
    ({"cost": 0.01}, "nonzero_or_unknown_reported_cost"),
    ({"cost": None}, "nonzero_or_unknown_reported_cost"),
    ({"cost": True}, "nonzero_or_unknown_reported_cost"),
    ({"model": "not-what-was-requested"}, "effective_model_mismatch"),
    ({"hash_override": "a" * 64}, "trace_hash_mismatch"),
    ({"text": False}, "missing_final_text_or_stop"),
    ({"stop": False}, "missing_final_text_or_stop"),
])
def test_receipt_missing_gates_fail_closed(tmp_path, changes, expected):
    receipt(tmp_path, **changes)
    row = report(tmp_path, catalog=presence())["endpoints"][0]
    assert row["classification"] == "not_qualified"
    assert row["execution"]["status"] == expected


def test_new_failed_attempt_overrides_older_success(tmp_path):
    path, _ = receipt(tmp_path, session="ses_good")
    os.utime(path, (NOW - 100, NOW - 100))
    receipt(tmp_path, session="ses_bad", status="failed")
    row = report(tmp_path, catalog=presence())["endpoints"][0]
    assert row["execution"]["status"] == "run_not_completed"
    assert row["execution"]["session_id"] == "ses_bad"


def test_stale_catalog_does_not_turn_history_into_authority(tmp_path):
    receipt(tmp_path)
    row = report(tmp_path, catalog=presence(NOW - 2000))["endpoints"][0]
    assert row["classification"] == "historical_receipt_only"
    assert row["automatic_dispatch_allowed"] is False


def test_local_residency_requires_fresh_health_props_kind_and_canary(tmp_path):
    entry = endpoint(kind="local_chat", model="qwen3.5-0.8b")
    observed = {
        "schema": "fleet-local-observations/v1",
        "observations": [{
            "endpoint_id": "model-a", "model": "qwen3.5-0.8b",
            "model_kind": "chat", "observed_epoch": NOW,
            "health": "ok", "loaded": True, "props_confirmed": True,
            "canary_pass": True, "secret": "DO_NOT_EXPOSE",
        }],
    }
    row = report(tmp_path, [entry], observations=observed)["endpoints"][0]
    assert row["classification"] == "chat_canary_observed"
    assert row["observation_authoritative"] is False
    assert row["production_admitted"] is False
    assert "DO_NOT_EXPOSE" not in json.dumps(row)
    observed["observations"][0]["observed_epoch"] = NOW - 901
    assert report(tmp_path, [entry], observations=observed)["endpoints"][0]["classification"] == "not_qualified"


def test_local_embeddings_never_masquerade_as_chat(tmp_path):
    entry = endpoint(kind="embedding", model="embed-model")
    obs = {"schema": "fleet-local-observations/v1", "observations": [{
        "endpoint_id": "model-a", "model": "embed-model", "observed_epoch": NOW,
        "health": "ok", "loaded": True, "props_confirmed": True,
        "canary_pass": True, "model_kind": "chat",
    }]}
    assert report(tmp_path, [entry], observations=obs)["endpoints"][0]["classification"] == "not_qualified"
    obs["observations"][0]["model_kind"] = "embedding"
    assert report(tmp_path, [entry], observations=obs)["endpoints"][0]["classification"] == "embedding_canary_observed"


def test_symlink_trace_is_not_trusted(tmp_path):
    _, trace = receipt(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-escaped")
    outside.write_bytes(trace.read_bytes())
    trace.unlink()
    trace.symlink_to(outside)
    assert report(tmp_path, catalog=presence())["endpoints"][0]["execution"]["status"] == "missing_or_untrusted_trace"


@pytest.mark.parametrize("model", ["auto", "router/default", "provider/free", "", "../bad", "model name"])
def test_aliases_and_unsafe_model_names_rejected(tmp_path, model):
    with pytest.raises(ValueError):
        report(tmp_path, [endpoint(model=model)])


def test_duplicate_endpoint_and_observation_ids_rejected(tmp_path):
    with pytest.raises(ValueError):
        report(tmp_path, [endpoint(), endpoint()])
    with pytest.raises(ValueError):
        report(tmp_path, observations={
            "schema": "fleet-local-observations/v1",
            "observations": [{"endpoint_id": "model-a"}, {"endpoint_id": "model-a"}],
        })


def test_manifest_session_identity_and_trace_session_id_checked(tmp_path):
    path, trace = receipt(tmp_path)
    doc = json.loads(path.read_text())
    doc["session_id"] = "other"
    path.write_text(json.dumps(doc))
    assert report(tmp_path, catalog=presence())["endpoints"][0]["execution"]["status"] == "manifest_session_mismatch"
    doc["session_id"] = path.stem
    trace.write_text(json.dumps({"type": "text", "sessionID": "other", "part": {"text": "wrong"}}) + "\n")
    doc["stdout"]["sha256"] = hashlib.sha256(trace.read_bytes()).hexdigest()
    path.write_text(json.dumps(doc))
    assert report(tmp_path, catalog=presence())["endpoints"][0]["execution"]["status"] == "trace_session_mismatch"


def test_report_does_not_confuse_config_with_evidence(tmp_path):
    row = report(tmp_path, [endpoint(kind="gateway", model="gateway-catalog")])["endpoints"][0]
    assert row["classification"] == "not_qualified"
    assert row["automatic_dispatch_allowed"] is False



def test_malformed_presence_is_not_treated_as_health(tmp_path):
    with pytest.raises(ValueError, match="presence"):
        report(tmp_path, catalog={"schema": "provider-presence/v1", "providers": []})
    with pytest.raises(ValueError, match="presence"):
        report(tmp_path, catalog={"providers": {}})


def test_unverified_upstream_group_never_grants_a_lease(tmp_path):
    declared = endpoint()
    declared["upstream_group"] = "supposed-unique-key"
    row = report(tmp_path, [declared], catalog=presence())["endpoints"][0]
    assert row["upstream_group_declared"] is True
    assert row["upstream_group_verified"] is False
    assert row["automatic_dispatch_allowed"] is False
