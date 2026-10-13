"""Ephemeral-key, multiprocess-safe durable HOLD research; no graph operations."""
from __future__ import annotations

import copy
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from runtime_projection_offline_admission_ledger import (
    OPT_IN,
    AdmissionHold,
    complete_without_independent_graph_witness,
    initialize,
    inspection,
    reserve_verified_intent,
)
from test_runtime_projection_signed_apply_gate import (
    NOW,
    manifest,
    package,
    public,
)


@pytest.fixture
def scenario(monkeypatch, tmp_path):
    monkeypatch.setenv(OPT_IN, "1")
    # Deployed software MUST NOT be allowed to bootstrap itself to 643.
    # Only an isolated synthetic genesis is provided in this fixture.
    ledger = tmp_path / "admission.sqlite3"
    initialize(ledger, trusted_genesis_generation=6)
    source = Ed25519PrivateKey.generate()
    reviewer = Ed25519PrivateKey.generate()
    m = manifest()
    return dict(
        db_path=ledger,
        manifest=m,
        attestation=package(m, source, reviewer),
        pinned_source_public_key=public(source),
        pinned_reviewer_public_key=public(reviewer),
        observed_now_ms=NOW,
    )


def test_verified_two_signer_intent_is_only_quarantined(scenario):
    intent = reserve_verified_intent(**scenario)
    assert intent.generation == 7
    assert intent.status == "PENDING_UNCERTAIN_GRAPH_OUTCOME"
    assert intent.graph_write_authorized is False
    state = inspection(scenario["db_path"])
    assert state == {
        "last_confirmed_generation": 6,
        "unresolved_pending": True,
        "pending_generation": 7,
        "replay_tombstone_count": 1,
        "graph_write_authorized": False,
        "auto_reconciliation_authorized": False,
    }


@pytest.mark.parametrize("mutation", ["source_sig","reviewer_sig","manifest","wrong_key",
                                      "stale_source","future_reviewer","nonmonotonic",
                                      "too_long_ttl","same_keys"])
def test_no_approval_written_on_invalid_or_stale_bundle(scenario,mutation):
    s = scenario.copy()
    s["manifest"] = copy.deepcopy(s["manifest"])
    s["attestation"] = copy.deepcopy(s["attestation"])
    if mutation == "source_sig":
        s["attestation"]["source"]["signature"] = "0" * 128
    elif mutation == "reviewer_sig":
        s["attestation"]["operator"]["signature"] = "0" * 128
    elif mutation == "manifest":
        s["manifest"]["approved_by"] = "forged"
    elif mutation == "wrong_key":
        s["pinned_source_public_key"] = public(Ed25519PrivateKey.generate())
    elif mutation == "stale_source":
        s["observed_now_ms"] = NOW + 301_000
    elif mutation == "future_reviewer":
        s["observed_now_ms"] = NOW - 11_000
    elif mutation == "nonmonotonic":
        source=Ed25519PrivateKey.generate()
        reviewer=Ed25519PrivateKey.generate()
        s["manifest"]["expected_current_generation"] = 7
        s["attestation"] = package(s["manifest"],source,reviewer)
        s["pinned_source_public_key"] = public(source)
        s["pinned_reviewer_public_key"] = public(reviewer)
    elif mutation == "too_long_ttl":
        source=Ed25519PrivateKey.generate()
        reviewer=Ed25519PrivateKey.generate()
        s["manifest"]["ttl_seconds"] = 86400
        s["attestation"] = package(s["manifest"],source,reviewer)
        s["pinned_source_public_key"] = public(source)
        s["pinned_reviewer_public_key"] = public(reviewer)
    elif mutation == "same_keys":
        s["pinned_reviewer_public_key"] = s["pinned_source_public_key"]
    with pytest.raises((AdmissionHold,ValueError)):
        reserve_verified_intent(**s)
    state = inspection(s["db_path"])
    assert state["replay_tombstone_count"] == 0
    assert state["unresolved_pending"] is False


def test_missing_optin_denies_before_creating_ledger(monkeypatch,tmp_path):
    monkeypatch.delenv(OPT_IN,raising=False)
    path=tmp_path/"not-created.sqlite3"
    with pytest.raises(AdmissionHold,match="OPT_IN"):
        initialize(path,trusted_genesis_generation=6)
    assert not path.exists()


def test_genesis_cannot_be_rebound_or_reset_after_issuance(scenario):
    reserve_verified_intent(**scenario)
    with pytest.raises(AdmissionHold,match="GENESIS_REBIND"):
        initialize(scenario["db_path"],trusted_genesis_generation=7)
    assert inspection(scenario["db_path"])["unresolved_pending"]


def test_next_approval_is_held_including_after_new_process_connection(scenario):
    reserve_verified_intent(**scenario)
    with pytest.raises(AdmissionHold,match="UNRESOLVED"):
        reserve_verified_intent(**scenario)
    assert inspection(scenario["db_path"])["replay_tombstone_count"] == 1
    with pytest.raises(AdmissionHold,match="INDEPENDENT_GRAPH_COMMIT"):
        complete_without_independent_graph_witness(
            scenario["db_path"], observed_generation=7,operator_claim=True)
    assert inspection(scenario["db_path"])["unresolved_pending"] is True


def test_8_parallel_same_signed_approval_requests_one_reservation(scenario):
    def attempt(_):
        try:
            return reserve_verified_intent(**scenario).status
        except AdmissionHold as e:
            return str(e)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results=list(executor.map(attempt,range(8)))
    assert results.count("PENDING_UNCERTAIN_GRAPH_OUTCOME") == 1
    assert results.count("UNRESOLVED_INFLIGHT_ATTEMPT_HOLD") == 7
    assert inspection(scenario["db_path"])["replay_tombstone_count"] == 1


def test_disposable_test_has_no_graph_endpoint_or_confirmation_method():
    source=(Path(__file__).with_name("runtime_projection_offline_admission_ledger.py")).read_text()
    for forbidden in ("GraphDatabase.driver", "NEO4J_URI", "NEO4J_PASSWORD",
                      "execute_write(", "execute_read(", "docker ",
                      "app.post(", "session.run(", "def commit(", "def reset("):
        assert forbidden not in source
    assert "NO_INDEPENDENT_GRAPH_COMMIT_PROOF_PERSIST_HOLD" in source


def test_requires_disposable_ledger_path(scenario,tmp_path):
    with pytest.raises(AdmissionHold,match="DISPOSABLE_LEDGER_PATH"):
        initialize(Path("/home/scott/production-approval.sqlite3"),
                   trusted_genesis_generation=0)




def test_ledger_file_mode_private_and_shared_copy_denied(scenario):
    db = scenario["db_path"]
    assert db.stat().st_mode & 0o077 == 0
    db.chmod(0o644)
    with pytest.raises(AdmissionHold, match="PRIVATE_DISPOSABLE_LEDGER"):
        inspection(db)
