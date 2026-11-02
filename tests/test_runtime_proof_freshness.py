"""Adversarial runtime-proof freshness tests, no production approval/Neo4j."""
import copy

import pytest
from test_runtime_admission import live_proof, profile

from assistx.runtime_admission import (
    MAX_LIVE_PROOF_AGE_MS,
    MAX_LIVE_PROOF_LIFETIME_MS,
    RuntimeAdmissionContractError,
    build_runtime_admission_candidate,
    validate_runtime_admission_candidate,
)


def build(live=None, *, now=1_100_000, ttl=300):
    return build_runtime_admission_candidate(
        profile(), live if live is not None else live_proof(),
        generation=897, expected_current_generation=896,
        approved_by="synthetic-only", approval_id="fixture-no-approval",
        ttl_seconds=ttl, now_ms=now,
    )


def test_bounded_observation_and_lifetime_contract():
    assert MAX_LIVE_PROOF_AGE_MS == 300_000
    assert MAX_LIVE_PROOF_LIFETIME_MS == 900_000
    c = build()
    assert c["lease"]["expires_at_ms"] == 1_300_000
    assert c["evidence"]["live_proof_observed_at_ms"] == 1_000_000
    assert c["lease"]["expires_at_ms"] < c["evidence"]["live_proof_expires_at_ms"]
    assert validate_runtime_admission_candidate(c, now_ms=1_100_000) is c


def test_stale_but_unexpired_proof_rejected():
    live = live_proof()
    live["observed_at_ms"] = 500_000
    live["expires_at_ms"] = 1_500_000
    with pytest.raises(RuntimeAdmissionContractError, match="maximum age"):
        build(live)


def test_far_future_expiry_cannot_extend_a_live_observation():
    live = live_proof()
    live["expires_at_ms"] = 2_000_000
    with pytest.raises(RuntimeAdmissionContractError, match="maximum observation lifetime"):
        build(live)


def test_proof_at_future_timestamp_rejected():
    live = live_proof()
    live["observed_at_ms"] = 1_100_001
    live["expires_at_ms"] = 1_200_000
    with pytest.raises(RuntimeAdmissionContractError, match="future"):
        build(live)


def test_original_proof_expiry_is_also_enforced():
    live = live_proof()
    live["expires_at_ms"] = 1_099_999
    with pytest.raises(RuntimeAdmissionContractError, match="expired"):
        build(live)


def test_revalidation_rejects_expired_candidate():
    candidate = build()
    with pytest.raises(RuntimeAdmissionContractError, match="expired"):
        validate_runtime_admission_candidate(candidate, now_ms=1_300_001)


def test_cannot_extend_lease_past_observation_age():
    candidate = build()
    candidate["lease"]["expires_at_ms"] = 1_400_000
    with pytest.raises(RuntimeAdmissionContractError, match="freshness bound"):
        validate_runtime_admission_candidate(candidate, now_ms=1_150_000)


def test_cannot_self_extend_live_proof_evidence_with_old_fingerprint():
    candidate = build()
    tampered = copy.deepcopy(candidate)
    tampered["evidence"]["live_proof_observed_at_ms"] = 1_100_000
    with pytest.raises(RuntimeAdmissionContractError, match="candidate fingerprint mismatch"):
        validate_runtime_admission_candidate(tampered, now_ms=1_150_000)


def test_short_remaining_age_requires_minimum_lease():
    c = build(now=1_270_000)
    assert c["lease"]["expires_at_ms"] == 1_300_000
    assert c["projection_manifest"]["ttl_seconds"] == 30
    with pytest.raises(RuntimeAdmissionContractError, match="minimum 30-second"):
        build(now=1_270_001)


def test_valid_candidate_remains_non_self_authorizing():
    c = build(now=1_150_000)
    assert c["profile"]["admission_enabled"] is False
    assert c["approval"]["require_lan_and_tailscale"] is True
    assert c["runtime"]["process_id"] == "4242"  # synthetic fixture only
    assert c["lease"]["revocable"] is True
    assert c["lease"]["expires_at_ms"] == 1_300_000
