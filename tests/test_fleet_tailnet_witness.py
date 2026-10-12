"""Tailnet witness sanitization and safe model-kind classification."""
import json

import pytest

from assistx.fleet_endpoint_harness import build_report
from assistx.fleet_tailnet_witness import adapt_tailnet_witness


def witness(state="resident_verified", kind="chat_model",
            proof="lmstudio_native_loaded_instances"):
    return {
        "schema_version": 1,
        "authority": "observational_only_not_runtime_admission",
        "captured_at": "2026-10-10T17:43:52+00:00",
        "source_inventory_sha256": "a" * 64,
        "observations": [{
            "node": "private-node", "ip": "100.100.1.5", "port": 1234,
            "service": "lmstudio_native", "state": state,
            "trace": ["PRIVATE_URL_SHOULD_NEVER_ESCAPE"],
            "models": [{"id": "prism-ml/bonsai-27b", "model_kind": kind,
                        "state": state, "proof": proof}],
        }],
    }


def test_live_witness_is_reduced_to_nonselectable_inventory(tmp_path):
    stock, observed, summary = adapt_tailnet_witness(witness())
    assert len(stock["endpoints"]) == 1
    assert summary["canaries_performed"] == 0
    assert summary["addresses_exported"] == 0
    assert stock["endpoints"][0]["id"].startswith("tailnet-")
    assert stock["endpoints"][0]["kind"] == "local_chat"
    assert observed["observations"][0]["canary_pass"] is False
    assert observed["observations"][0]["props_confirmed"] is True
    assert "100.100.1.5" not in json.dumps((stock, observed, summary))
    assert "PRIVATE_URL_SHOULD_NEVER_ESCAPE" not in json.dumps((stock, observed, summary))
    report = build_report(stock, local_observations=observed, state_root=tmp_path,
                          now=1791654500, freshness_seconds=900)
    row = report["endpoints"][0]
    assert row["classification"] == "resident_observed"
    assert row["automatic_dispatch_allowed"] is False
    assert row["observation_authoritative"] is False


def test_witness_stales_out_without_refreshing_itself(tmp_path):
    stock, observed, _ = adapt_tailnet_witness(witness())
    report = build_report(stock, local_observations=observed, state_root=tmp_path,
                          now=1791664500, freshness_seconds=900)
    assert report["endpoints"][0]["classification"] == "not_qualified"


def test_installed_not_loaded_is_not_resident(tmp_path):
    stock, observed, _ = adapt_tailnet_witness(witness(state="installed_not_loaded"))
    report = build_report(stock, local_observations=observed, state_root=tmp_path,
                          now=1791654500)
    assert report["endpoints"][0]["classification"] == "not_qualified"
    assert observed["observations"][0]["loaded"] is False


def test_missing_native_proof_not_resident(tmp_path):
    stock, observed, _ = adapt_tailnet_witness(witness(proof="unrecognized"))
    report = build_report(stock, local_observations=observed, state_root=tmp_path,
                          now=1791654500)
    assert observed["observations"][0]["props_confirmed"] is False
    assert report["endpoints"][0]["classification"] == "not_qualified"


def test_unverified_model_kind_is_never_misclassified_as_chat():
    stock, observed, summary = adapt_tailnet_witness(witness(kind="unverified"))
    assert not stock["endpoints"]
    assert not observed["observations"]
    assert summary["skipped_unknown_or_duplicate"] == 1


def test_embedding_residency_is_not_chat_eligibility(tmp_path):
    stock, observed, _ = adapt_tailnet_witness(witness(kind="embedding_model"))
    report = build_report(stock, local_observations=observed, state_root=tmp_path,
                          now=1791654500)
    assert stock["endpoints"][0]["kind"] == "embedding"
    assert report["endpoints"][0]["classification"] == "embedding_resident_observed"
    assert report["endpoints"][0]["automatic_dispatch_allowed"] is False


@pytest.mark.parametrize("override", [
    {"authority": "runtime_admission"},
    {"schema_version": 2},
    {"observations": [None]},
    {"captured_at": "unparseable"},
])
def test_untrusted_or_invalid_witness_rejected(override):
    data = witness()
    data.update(override)
    with pytest.raises(ValueError):
        adapt_tailnet_witness(data)


def test_duplicate_models_are_safely_deduplicated():
    data = witness()
    data["observations"][0]["models"] *= 2
    stock, observed, summary = adapt_tailnet_witness(data)
    assert len(stock["endpoints"]) == len(observed["observations"]) == 1
    assert summary["skipped_unknown_or_duplicate"] == 1
