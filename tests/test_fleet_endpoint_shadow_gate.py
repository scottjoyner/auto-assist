"""Deny-only lease and authenticated-observation source-binding acceptance."""
from datetime import datetime, timezone
import hashlib
import json

import pytest

from assistx.fleet_endpoint_shadow_gate import (
    bind_mobile_projection, shadow_quota_assessment,
)

NOW = datetime(2026, 10, 10, 17, 55, tzinfo=timezone.utc).timestamp()


def harness():
    return {
        "schema": "fleet-endpoint-harness-report/v1",
        "endpoints": [
            {"id": "cloud", "kind": "cloud_chat", "provider": "kilo_free",
             "model": "cohere/north-mini-code:free", "classification": "local_receipt_pass"},
            {"id": "local", "kind": "local_chat", "provider": "tailnet_native",
             "model": "example-model", "classification": "resident_observed"},
        ],
    }


def snapshot(**extra):
    base = {
        "schema": "provider-lease-status/v1",
        "authority": "single_host_prototype_only",
        "audit_integrity": True,
        "captured_at": "2026-10-10T17:54:00Z",
        "providers": {"kilo": {"active": 0, "slots": 1, "circuit_open": False}},
    }
    return {**base, **extra}


def test_free_slot_never_becomes_execution_authority():
    outcome = shadow_quota_assessment(harness(), snapshot(), now=NOW)
    cloud, local = outcome["endpoints"]
    assert cloud["local_capacity_observed"] is True
    assert cloud["quota_group"] == "kilo"
    assert cloud["may_dispatch"] is False
    assert "missing_authenticated_cross_node_lease" in cloud["reasons"]
    assert local["may_dispatch"] is False
    assert outcome["authorizations_granted"] == 0
    assert outcome["live_lease_actions"] == outcome["provider_requests"] == 0


@pytest.mark.parametrize("mutations,expected", [
    ({"audit_integrity": False}, "lease_snapshot_untrusted_or_stale"),
    ({"authority": "cluster_lease_authorized"}, "lease_snapshot_untrusted_or_stale"),
    ({"captured_at": "2026-10-10T17:30:00Z"}, "lease_snapshot_untrusted_or_stale"),
    ({"providers": {"kilo": {"active": 1, "slots": 1, "circuit_open": False}}},
     "local_lease_slots_full"),
    ({"providers": {"kilo": {"active": 0, "slots": 1, "circuit_open": True}}},
     "lease_circuit_open"),
    ({"providers": {"kilo": {"active": "0", "slots": True, "circuit_open": False}}},
     "invalid_lease_group_counters"),
    ({"providers": {"different-upstream": {"active": 0, "slots": 1, "circuit_open": False}}},
     "lease_group_not_observed"),
])
def test_lease_snapshot_fails_closed(mutations, expected):
    verdict = shadow_quota_assessment(harness(), snapshot(**mutations), now=NOW)
    cloud = verdict["endpoints"][0]
    assert expected in cloud["reasons"]
    assert cloud["local_capacity_observed"] is False
    assert cloud["admission_decision"] == "DENY"


def test_unidentified_provider_cannot_invent_quota_group():
    data = harness()
    data["endpoints"][0]["provider"] = "openrouter"
    row = shadow_quota_assessment(data, snapshot(), now=NOW)["endpoints"][0]
    assert row["quota_group"] == "unverified"
    assert "unknown_upstream_quota_alias" in row["reasons"]
    assert row["may_dispatch"] is False


def test_failed_inference_and_no_lease_snapshot_are_both_denied():
    data = harness()
    data["endpoints"][0]["classification"] = "not_qualified"
    row = shadow_quota_assessment(data, None, now=NOW)["endpoints"][0]
    assert "no_fresh_locally_verified_execution" in row["reasons"]
    assert "lease_snapshot_untrusted_or_stale" in row["reasons"]


def witness_payload():
    return {
        "schema_version": 1,
        "authority": "observational_only_not_runtime_admission",
        "source_inventory_sha256": "c" * 64,
        "captured_at": "2026-10-10T17:54:30Z",
        "observations": [{
            "node": "PRIVATE_HOST", "ip": "100.64.1.3",
            "state": "resident_verified", "port": 1234,
            "models": [{
                "id": "prism-ml/bonsai-27b", "state": "resident_verified",
                "model_kind": "chat_model", "proof": "llamacpp_health_props",
            }],
        }],
    }


def binding_fixture():
    raw = json.dumps(witness_payload(), sort_keys=True).encode()
    public = {
        "schema_version": 1, "source": "tailnet_observational_only",
        "authority": "not_admitted", "state": "fresh",
        "observed_nodes": 1, "observed_endpoints": 1,
        "resident_endpoints": 1, "truncated": False,
        "observation_sha256": hashlib.sha256(raw).hexdigest(),
        "source_inventory_sha256": "c" * 64,
        "models": [{
            "display_name": "bonsai-27b", "state": "resident_verified",
            "observed_instances": 1, "admitted": False, "selectable": False,
        }],
    }
    return raw, public


def test_mobile_projection_binds_exact_private_source_without_leaking():
    raw, public = binding_fixture()
    result = bind_mobile_projection(public, raw, now=NOW)
    assert result["status"] == "LOCAL_SOURCE_BINDING_PASS"
    assert result["source_binding_valid"] is True
    assert result["transport_authenticated"] is False
    assert result["execution_authorized"] is False
    assert result["runnable_models"] == 0
    assert "PRIVATE_HOST" not in json.dumps(result)
    assert "100.64.1.3" not in json.dumps(result)


@pytest.mark.parametrize("tamper,expected", [
    ("hash", "private_public_observation_digest_mismatch"),
    ("source", "private_public_source_digest_mismatch"),
    ("name", "public_models_not_source_bound"),
    ("selectable", "public_models_not_source_bound"),
    ("count", "observation_counts_disagree"),
    ("authority", "public_projection_not_fresh_or_observational"),
])
def test_mobile_projection_tampering_detected(tamper, expected):
    raw, public = binding_fixture()
    if tamper == "hash":
        public["observation_sha256"] = "f" * 64
    elif tamper == "source":
        public["source_inventory_sha256"] = "f" * 64
    elif tamper == "name":
        public["models"][0]["display_name"] = "forged"
    elif tamper == "selectable":
        public["models"][0]["selectable"] = True
    elif tamper == "count":
        public["observed_endpoints"] = 999
    elif tamper == "authority":
        public["authority"] = "admitted"
    result = bind_mobile_projection(public, raw, now=NOW)
    assert result["status"] == "REJECT"
    assert result["reasons"] == [expected]


def test_future_and_stale_private_witness_rejected():
    raw, public = binding_fixture()
    assert bind_mobile_projection(public, raw, now=NOW + 901)["status"] == "REJECT"
    assert bind_mobile_projection(public, raw, now=NOW - 901)["status"] == "REJECT"


def test_model_kind_is_not_exposed_as_execution_proof():
    raw, public = binding_fixture()
    public["models"][0]["admitted"] = True
    result = bind_mobile_projection(public, raw, now=NOW)
    assert result["status"] == "REJECT"


def test_mobile_projection_fails_on_unexpected_fields_and_node_counts():
    raw, public = binding_fixture()
    public["base_url"] = "http://private-endpoint:1234"
    result = bind_mobile_projection(public, raw, now=NOW)
    assert result["status"] == "REJECT"
    assert result["reasons"] == ["unrecognized_public_projection_fields"]

    raw, public = binding_fixture()
    public["observed_nodes"] = 999
    result = bind_mobile_projection(public, raw, now=NOW)
    assert result["status"] == "REJECT"
    assert result["reasons"] == ["observation_counts_disagree"]
