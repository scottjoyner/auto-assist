"""Round-6 hermetic contracts for the runtime-admission generation fence.

Offline and bounded: every case exercises only pure validation logic in
``assistx.runtime_admission`` with in-memory dicts. No service, no database,
no Neo4j, no network, no credential use. Fixtures are reused from
``test_runtime_admission`` (the canonical happy-path fixture module); this file
adds only the *negative / monotonicity* group that module does not cover:

* generation must be exactly ``expected_current_generation + 1``
* generation and expected-generation inputs are type/range checked
* TTL bounds (30..900 s) are enforced
* approval identity is required (no placeholder values)
* the live proof must stay fresh for the whole minimum 30 s lease
* forging a generation after build is caught by the fingerprint check
"""

from __future__ import annotations

import copy

import pytest
from test_runtime_admission import live_proof, profile

from assistx.runtime_admission import (
    RuntimeAdmissionContractError,
    build_runtime_admission_candidate,
    validate_runtime_admission_candidate,
)

_APPROVAL = {
    "approved_by": "operator",
    "approval_id": "approval-r6",
    "ttl_seconds": 300,
    "now_ms": 1_100_000,
}


def _build(*, generation: int = 1, expected: int = 0, **overrides) -> dict:
    kwargs = dict(_APPROVAL)
    kwargs.update(generation=generation, expected_current_generation=expected)
    kwargs.update(overrides)
    return build_runtime_admission_candidate(profile(), live_proof(), **kwargs)


@pytest.mark.parametrize(
    "generation, expected",
    [
        (0, 0),  # zero is not a valid generation
        (2, 0),  # skipped one
        (3, 1),  # gap of two
        (5, 5),  # replay of the current generation
        (4, 6),  # stale / behind the current generation
    ],
)
def test_generation_must_be_exactly_expected_plus_one(generation, expected):
    """No skip, no gap, no replay: the fence admits only expected + 1."""
    with pytest.raises(RuntimeAdmissionContractError, match="generation must"):
        _build(generation=generation, expected=expected)


@pytest.mark.parametrize(
    "generation, expected, message",
    [
        (-1, 0, "generation must be positive"),
        ("not-an-int", 0, "generation must be an integer"),
        (1, -1, "expected_current_generation must be zero or positive"),
        (1, "x", "expected_current_generation must be an integer"),
    ],
)
def test_generation_inputs_are_type_and_range_checked(generation, expected, message):
    with pytest.raises(RuntimeAdmissionContractError, match=message):
        _build(generation=generation, expected=expected)


def test_accepted_generation_is_carried_consistently_on_every_surface():
    candidate = _build(generation=7, expected=6)

    assert candidate["approval"]["generation"] == 7
    assert candidate["approval"]["expected_current_generation"] == 6
    assert candidate["projection_manifest"]["generation"] == 7
    assert candidate["projection_manifest"]["expected_current_generation"] == 6
    assert candidate["lease"]["generation"] == 7
    assert candidate["lease"]["lease_id"].startswith("runtime-admission-lease:7:")
    assert candidate["projection_manifest"]["revision"].startswith("profile:")
    # The freshly built candidate validates unchanged.
    assert validate_runtime_admission_candidate(candidate, now_ms=1_100_000) is candidate


@pytest.mark.parametrize("ttl", [0, -5, 1, 29, 901, 5000])
def test_ttl_out_of_range_is_rejected(ttl):
    with pytest.raises(RuntimeAdmissionContractError, match="ttl_seconds"):
        _build(ttl_seconds=ttl)


@pytest.mark.parametrize("field", ["approved_by", "approval_id"])
@pytest.mark.parametrize("value", ["", None, 0, "replace-me", "unknown"])
def test_approval_identity_is_required(field, value):
    with pytest.raises(RuntimeAdmissionContractError, match="approved_by and approval_id"):
        _build(**{field: value})


def test_live_proof_must_stay_fresh_for_the_minimum_lease():
    proof = live_proof()
    # The proof is still unexpired at now_ms, but only for 10 s while ttl is
    # 300 s: the effective projection lease would be below the 30 s minimum.
    proof["expires_at_ms"] = 1_100_000 + 10_000
    with pytest.raises(RuntimeAdmissionContractError, match="minimum 30-second lease"):
        build_runtime_admission_candidate(
            profile(),
            proof,
            generation=1,
            expected_current_generation=0,
            approved_by="operator",
            approval_id="approval-r6",
            ttl_seconds=300,
            now_ms=1_100_000,
        )


def test_forging_generation_after_build_is_detected():
    candidate = _build(generation=1, expected=0)
    forged = copy.deepcopy(candidate)
    forged["approval"]["generation"] = 2
    # approval is part of the fingerprinted core, so the forged document
    # cannot pass validation even though its individual fields look plausible.
    with pytest.raises(RuntimeAdmissionContractError, match="fingerprint mismatch"):
        validate_runtime_admission_candidate(forged, now_ms=1_100_000)
