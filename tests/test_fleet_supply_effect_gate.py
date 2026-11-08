"""Read-only synthetic canonical projection+WorkSupply effect-time checks."""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from assistx.fleet_supply_effect_gate import current_supply_permits_effect

NOW = 1_794_104_000_000


@dataclass(frozen=True)
class Projection:
    generation: int = 897
    total_slots: int = 2
    expires_at_ms: int = NOW + 90_000


class Row:
    def __init__(self, fields):
        self.fields = fields

    def single(self, *, strict=False):
        assert strict is True
        if isinstance(self.fields, list):
            if len(self.fields) != 1:
                raise ValueError("ambiguous canonical state")
            return self.fields[0]
        return self.fields


class Session:
    def __init__(self, fields):
        self.fields = fields
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def run(self, statement):
        self.queries.append(statement)
        assert "MATCH (p:FleetProjectionState {name:'canonical'})" in statement
        assert "OPTIONAL MATCH (w:WorkSupplyState {name:'canonical'})" in statement
        upper = statement.upper()
        assert not any(token in upper for token in (" SET ", " CREATE ", " DELETE ", " MERGE ", " REMOVE "))
        return Row(self.fields)


class Neo:
    def __init__(self, fields):
        self.fields = fields
        self.session = None

    def _session(self):
        self.session = Session(self.fields)
        return self.session


def valid():
    return {
        "generation": 897,
        "status": "approved",
        "expires_at_ts": NOW + 90_000,
        "owner_receipt": "a" * 64,
        "supply_generated_at_ts": NOW - 2_000,
        "supply_generation": 897,
        "supply_slots": 2,
    }


def test_authoritative_matching_state_allows_no_mutation():
    neo = Neo(valid())
    decision = current_supply_permits_effect(neo, Projection(), now_ms=NOW)
    assert decision.allowed is True
    assert decision.reason == "matching_live_controller_and_owner_receipt"
    assert len(neo.session.queries) == 1


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("generation", 896, "canonical_generation_or_lease_changed"),
        ("generation", True, "canonical_generation_or_lease_changed"),
        ("status", "pending", "canonical_owner_grant_missing"),
        ("status", None, "canonical_owner_grant_missing"),
        ("owner_receipt", None, "canonical_owner_grant_missing"),
        ("owner_receipt", "c" * 63, "canonical_owner_grant_missing"),
        ("owner_receipt", "Z" * 64, "canonical_owner_grant_missing"),
        ("expires_at_ts", NOW, "canonical_generation_or_lease_changed"),
        ("expires_at_ts", "1794104100000", "canonical_generation_or_lease_changed"),
        ("supply_generation", 896, "work_supply_generation_or_capacity_mismatch"),
        ("supply_generation", None, "work_supply_generation_or_capacity_mismatch"),
        ("supply_slots", 0, "work_supply_generation_or_capacity_mismatch"),
        ("supply_slots", 3, "work_supply_generation_or_capacity_mismatch"),
        ("supply_slots", True, "work_supply_generation_or_capacity_mismatch"),
        ("supply_generated_at_ts", 0, "work_supply_clock_invalid"),
        ("supply_generated_at_ts", NOW + 20_000, "work_supply_clock_invalid"),
        ("supply_generated_at_ts", NOW - 2_000_000, "work_supply_heartbeat_expired"),
    ],
)
def test_fails_closed_on_invalid_controller_or_grant(field, value, reason):
    state = valid()
    state[field] = value
    decision = current_supply_permits_effect(Neo(state), Projection(), now_ms=NOW)
    assert decision.allowed is False
    assert decision.reason == reason


@pytest.mark.parametrize("fields", [None, {}, []])
def test_absent_graph_state_denies(fields):
    assert current_supply_permits_effect(Neo(fields), Projection(), now_ms=NOW).allowed is False


def test_duplicate_canonical_state_fails_closed_even_when_one_record_looks_valid():
    neo = Neo([valid(), {**valid(), "generation": 896}])
    decision = current_supply_permits_effect(neo, Projection(), now_ms=NOW)
    assert decision.allowed is False
    assert decision.reason == "authoritative_graph_unavailable"
    assert "LIMIT 2" in neo.session.queries[0]


def test_read_error_and_no_graph_session_fail_closed():
    class Broken:
        def _session(self):
            raise RuntimeError("synthetic graph read failure")

    assert current_supply_permits_effect(Broken(), Projection(), now_ms=NOW).reason == "authoritative_graph_unavailable"
    assert current_supply_permits_effect(object(), Projection(), now_ms=NOW).allowed is False


def test_cached_projection_invalid_even_if_graph_healthy():
    assert (
        current_supply_permits_effect(Neo(valid()), Projection(expires_at_ms=NOW), now_ms=NOW).reason
        == "cached_projection_invalid"
    )
    assert (
        current_supply_permits_effect(Neo(valid()), Projection(generation=0), now_ms=NOW).reason
        == "cached_projection_invalid"
    )
    assert (
        current_supply_permits_effect(Neo(valid()), Projection(total_slots=0), now_ms=NOW).reason
        == "cached_projection_invalid"
    )
    assert current_supply_permits_effect(Neo(valid()), object(), now_ms=NOW).reason == "cached_projection_invalid"


def test_background_supply_false_does_not_erase_foreground_capacity():
    # WorkSupply.allow_background controls queue *generation*, not permission
    # for already-approved foreground tasks; the gate intentionally ignores it.
    state = valid()
    state["allow_background"] = False
    assert current_supply_permits_effect(Neo(state), Projection(), now_ms=NOW).allowed


def test_future_clock_rejected():
    assert current_supply_permits_effect(Neo(valid()), Projection(), now_ms=0).reason == "invalid_clock"
    assert current_supply_permits_effect(Neo(valid()), Projection(), now_ms=True).reason == "invalid_clock"
