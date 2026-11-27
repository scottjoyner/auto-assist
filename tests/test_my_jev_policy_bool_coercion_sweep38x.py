"""Hermetic contract for my-jev policy boolean coercion (sweep38x).

Every value below is an in-memory Python literal. ``build_policy_request`` is a
pure builder (no HTTP call), and all environment flags it reads are pinned by
the fixture. No service, credential, database, graph or outbox is touched.
"""

from __future__ import annotations

import pytest

from assistx.my_jev_policy import _coerce_bool, build_policy_request


@pytest.fixture(autouse=True)
def _pinned_env(monkeypatch):
    monkeypatch.setenv("MY_JEV_SHADOW_ACTIONS_ALLOWED", "false")
    monkeypatch.setenv("MY_JEV_SHADOW_EXTERNAL_ACTIONS_ALLOWED", "false")
    monkeypatch.setenv("MY_JEV_SHADOW_PRIVILEGED_ACTIONS_ALLOWED", "false")
    monkeypatch.setenv("MY_JEV_SHADOW_LOCAL_WRITES_ALLOWED", "false")
    monkeypatch.setenv("MY_JEV_SHADOW_APPROVAL_GATE_AVAILABLE", "true")


# --- helper-level contract --------------------------------------------------


@pytest.mark.parametrize("value", [True, False])
def test_real_booleans_pass_through(value):
    assert _coerce_bool(value) is value


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "verified", "TRUE", " yes "])
def test_recognized_true_strings_remain_true(value):
    assert _coerce_bool(value) is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "unknown", "2"])
def test_other_strings_remain_false(value):
    assert _coerce_bool(value) is False


@pytest.mark.parametrize("default", [True, False])
def test_none_uses_caller_default(default):
    assert _coerce_bool(None, default=default) is default


@pytest.mark.parametrize(
    "value",
    [
        1,
        2,
        -1,
        [False],
        ["false"],
        {"verified": False},
        (),
        (False,),
    ],
)
def test_nonboolean_json_shapes_never_become_true(value):
    assert _coerce_bool(value) is False


# --- policy-request boundary ------------------------------------------------


def _request(metadata_json):
    return build_policy_request({"id": "synthetic", "text": "probe", "metadata_json": metadata_json})


def _verdicts(metadata_json):
    request = _request(metadata_json)
    return (
        request["state"]["speaker_verified"],
        request["state"]["foreground"],
        request["constraints"]["speaker_verified"],
    )


@pytest.mark.parametrize("value", [[False], {"verified": False}, 2, ["false"], (False,)])
def test_speaker_verified_list_object_or_number_does_not_assert_trust(value):
    assert _verdicts({"speaker_verified": value}) == (False, True, False)


@pytest.mark.parametrize("value", [[False], {"foreground": False}, 2])
def test_foreground_list_object_or_number_does_not_assert_foreground(value):
    assert _verdicts({"foreground": value}) == (False, False, False)


def test_real_boolean_speaker_verification_is_preserved():
    assert _verdicts({"speaker_verified": True}) == (True, True, True)
    assert _verdicts({"speaker_verified": False}) == (False, True, False)


def test_string_verified_speaker_is_preserved():
    assert _verdicts({"speaker_verified": "verified"}) == (True, True, True)
    assert _verdicts({"speaker_verified": "false"}) == (False, True, False)


def test_absent_fields_keep_documented_defaults():
    assert _verdicts({}) == (False, True, False)


def test_request_shape_unchanged_for_valid_booleans():
    request = _request({"speaker_verified": True, "foreground": True})
    assert request["constraints"]["actions_allowed"] is False
    assert request["constraints"]["approval_gate_available"] is True
    assert request["state"]["available_capabilities"] == [
        "chat",
        "task_graph",
        "tools",
        "delegate",
    ]
