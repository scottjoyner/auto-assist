"""Deny-by-default release overlay tests, no Docker daemon or live secrets."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts" / "inspect_api_only_source_bind_release.py"
SPEC = importlib.util.spec_from_file_location("api_source_release_preflight", PATH)
assert SPEC is not None and SPEC.loader is not None
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


@pytest.fixture
def before():
    return {
        "name": "synthetic-release",
        "services": {
            "api": {
                "image": "synthetic-api:restricted",
                "volumes": [
                    {"type": "bind", "source": "/disposable/src", "target": "/app/src", "read_only": False},
                    {"type": "volume", "source": "state", "target": "/app/artifacts"},
                    {"type": "bind", "source": "/disposable/config", "target": "/app/config", "read_only": True},
                ],
                "environment": {"TOKEN": "test-dummy-NOT_A_SECRET"},
                "networks": {"backend": {}},
                "restart": "unless-stopped",
            },
            "worker": {"image": "worker:old", "volumes": [{"target": "/app/src", "type": "bind", "source": "/disposable/src"}]},
            "hermes-adapter": {"image": "hermes:old", "volumes": [{"target": "/app/src", "type": "bind", "source": "/disposable/src"}]},
        },
        "networks": {"backend": {"name": "isolated-example"}},
        "volumes": {"state": {"name": "synthetic-state"}},
    }


def test_accept_only_api_bind_removal(before):
    retained = subject.api_source_free_mounts(before)
    after = copy.deepcopy(before)
    after["services"]["api"]["volumes"] = retained
    receipt = subject.assert_only_api_source_removed(before, after, retained)
    assert receipt["verdict"] == "API_ONLY_SOURCE_BIND_REMOVAL_RENDER_PASS_NOT_DEPLOYMENT"
    assert receipt["old_mount_count"] == 3
    assert receipt["new_mount_count"] == 2
    assert receipt["physical_rollback_verified"] is False
    assert receipt["release_authorized"] is False
    assert after["services"]["worker"] == before["services"]["worker"]


@pytest.mark.parametrize("mutation", [
    lambda a: a["services"]["api"].update({"environment": {"TOKEN": "different"}}),
    lambda a: a["services"]["api"].update({"restart": "no"}),
    lambda a: a["services"]["api"]["volumes"].pop(),
    lambda a: a["services"]["worker"].update({"volumes": []}),
    lambda a: a["services"]["hermes-adapter"].update({"image": "different"}),
    lambda a: a["networks"]["backend"].update({"name": "unexpected"}),
    lambda a: a["volumes"]["state"].update({"name": "not-the-original"}),
])
def test_deny_any_other_change(before, mutation):
    retained = subject.api_source_free_mounts(before)
    after = copy.deepcopy(before)
    after["services"]["api"]["volumes"] = retained
    mutation(after)
    with pytest.raises(subject.PreflightHold, match="RENDERED_COMPOSE_DRIFT_HOLD"):
        subject.assert_only_api_source_removed(before, after, retained)


@pytest.mark.parametrize("replacement", [[], None, [
    {"type": "bind", "target": "/app/src", "source": "/disposable/first"},
    {"type": "bind", "target": "/app/src", "source": "/disposable/second"},
], [
    {"type": "volume", "target": "/app/src", "source": "state"},
    {"type": "volume", "target": "/data", "source": "state"},
]])
def test_deny_unexpected_source_mount(before, replacement):
    before["services"]["api"]["volumes"] = replacement
    with pytest.raises(subject.PreflightHold, match="UNEXPECTED_SOURCE_BIND_TOPOLOGY"):
        subject.api_source_free_mounts(before)


def test_never_accept_missing_api_source_bind(before):
    retained = subject.api_source_free_mounts(before)
    before["services"]["api"]["volumes"] = retained
    with pytest.raises(subject.PreflightHold, match="UNEXPECTED_SOURCE_BIND_TOPOLOGY"):
        subject.api_source_free_mounts(before)


def test_no_secret_or_path_in_hold_receipt():
    # Error codes are fixed, never leak a rendered value or subprocess stderr.
    assert "TOKEN" not in str(subject.PreflightHold("RENDERED_COMPOSE_DRIFT_HOLD"))
