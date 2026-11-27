"""Hermetic r57 regression: an inference policy's ``enabled`` flag must be a
real boolean.  Python truthiness otherwise turns an explicit string
``"false"`` into an *enabled* policy, so a disabled policy is silently
compiled into live trials.  No network, DB, Redis, Neo4j, service or
credential access; all fixtures are in-memory dicts.
"""

from __future__ import annotations

import pytest

from assistx.inference_policy_experiment import compile_trials, validate_policy


def _policy(**overrides):
    raw = {
        "policy_id": "p-r57",
        "node_id": "node-r57",
        "model_handle": "local-model",
        "backend": "llama_cpp",
        "quantization": "q4_k_m",
        "speculation": "none",
        "context_tokens": 4096,
        "concurrency": 1,
        "endpoint_env": "R57_ENDPOINT",
    }
    raw.update(overrides)
    return raw


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "off", "no"])
def test_string_disabled_flag_is_not_enabled(value):
    out = validate_policy(_policy(enabled=value))
    assert out["enabled"] is False


@pytest.mark.parametrize("value", [2, "garbage", {"x": 1}, ["x"], object()])
def test_malformed_enabled_flag_is_rejected(value):
    with pytest.raises(ValueError):
        validate_policy(_policy(enabled=value))


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"enabled": True}, True),
        ({"enabled": False}, False),
        ({"enabled": "true"}, True),
        ({}, True),
    ],
)
def test_legitimate_enabled_values_are_preserved(kwargs, expected):
    out = validate_policy(_policy(**kwargs))
    assert out["enabled"] is expected


def _case():
    return {"task_family": "coding", "case_sha256": "case-r57"}


def test_compile_trials_does_not_admit_truthy_string_disabled_policy():
    matrix = {"policies": [_policy(enabled="false", policy_sha256="p-r57-sha")]}
    assert compile_trials([_case()], matrix) == []


def test_compile_trials_still_admits_explicitly_enabled_policy():
    matrix = {"policies": [_policy(enabled=True, policy_sha256="p-r57-sha")]}
    trials = compile_trials([_case()], matrix)
    assert len(trials) == 1
    assert trials[0].policy["enabled"] is True


def test_compile_trials_rejects_explicitly_disabled_policy():
    matrix = {"policies": [_policy(enabled=False, policy_sha256="p-r57-sha")]}
    assert compile_trials([_case()], matrix) == []
