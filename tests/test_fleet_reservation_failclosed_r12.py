"""Fail-closed fleet reservation loading (r12).

``fleet.select_any`` skips nodes reserved for interactive Hermes sessions by
reading ``fleet_reservations.json``.  Before this fix ``_load_reservations``
swallowed *every* exception and returned ``{}``, so a corrupt, unreadable or
wrongly-shaped file silently dropped all reservations and background swarm work
could be routed onto a node reserved for a human.

These tests are hermetic: the reservation path is redirected into ``tmp_path``
by replacing the module's ``__file__``; no real fleet state, network or Redis is
touched.
"""

from __future__ import annotations

import json

import pytest

import assistx.fleet as fleet

# Resolves against the unmodified base too, so the fail-before run can collect
# these tests (there the attribute simply does not exist).
ReservationFileError = getattr(fleet, "ReservationFileError", RuntimeError)


@pytest.fixture
def reservation_file(tmp_path, monkeypatch):
    """Point fleet's reservation file at an isolated tmp path."""
    module_file = tmp_path / "fleet.py"
    module_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(fleet, "__file__", str(module_file))
    return tmp_path / "fleet_reservations.json"


# --------------------------------------------------------------------------- #
# _load_reservations: fail-closed invariants
# --------------------------------------------------------------------------- #
def test_missing_file_means_no_reservations(reservation_file):
    assert not reservation_file.exists()
    assert fleet._load_reservations() == {}


def test_valid_object_round_trips_unchanged(reservation_file):
    snapshot = {
        "x1-370:1234": {
            "by": "hermes-session-1",
            "until": 9_999_999_999.0,
            "purpose": "interactive",
        },
        "beelink:1234": {"by": "hermes-session-2", "until": 1.0},
    }
    reservation_file.write_text(json.dumps(snapshot), encoding="utf-8")
    assert fleet._load_reservations() == snapshot


def test_malformed_json_fails_closed(reservation_file):
    reservation_file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ReservationFileError, match="cannot read fleet reservations"):
        fleet._load_reservations()


def test_empty_file_fails_closed(reservation_file):
    reservation_file.write_text("", encoding="utf-8")
    with pytest.raises(ReservationFileError):
        fleet._load_reservations()


def test_top_level_list_fails_closed(reservation_file):
    reservation_file.write_text('["x1-370:1234"]', encoding="utf-8")
    with pytest.raises(ReservationFileError, match="must be a JSON object"):
        fleet._load_reservations()


def test_top_level_scalar_fails_closed(reservation_file):
    reservation_file.write_text("42", encoding="utf-8")
    with pytest.raises(ReservationFileError, match="must be a JSON object"):
        fleet._load_reservations()


def test_directory_at_reservation_path_fails_closed(reservation_file):
    reservation_file.mkdir()
    with pytest.raises(ReservationFileError):
        fleet._load_reservations()


# --------------------------------------------------------------------------- #
# select_any: unreadable reservations suppress auto-dispatch
# --------------------------------------------------------------------------- #
def test_select_any_suppresses_dispatch_when_reservations_unreadable(
    monkeypatch,
):
    def _boom():
        raise ReservationFileError("cannot read fleet reservations: synthetic")

    monkeypatch.setattr(fleet, "_ensure_refresh_thread", lambda: None)
    monkeypatch.setattr(fleet, "discover", lambda: None)
    monkeypatch.setattr(fleet, "_load_reservations", _boom)

    assert fleet.select_any([], task={}) == (None, None)


def test_select_any_still_uses_valid_snapshot(monkeypatch):
    """The fail-closed branch must not swallow a healthy reservation read."""
    seen = {}

    def _fake_load():
        seen["called"] = True
        return {}

    monkeypatch.setattr(fleet, "_ensure_refresh_thread", lambda: None)
    monkeypatch.setattr(fleet, "discover", lambda: None)
    monkeypatch.setattr(fleet, "_load_reservations", _fake_load)
    monkeypatch.setattr(fleet, "_model_map", {})

    # Empty model map -> no candidate; the point is the snapshot was consumed
    # normally instead of tripping the fail-closed early return.
    assert fleet.select_any([], task={}) == (None, None)
    assert seen == {"called": True}
