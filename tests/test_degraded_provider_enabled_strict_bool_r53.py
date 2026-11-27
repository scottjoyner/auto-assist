"""Round 53: the degraded-plane provider ``enabled`` flag must be a real bool.

The degraded control plane only delegates to *private local* providers. Both the
base admission helper (``degraded_control_plane._is_local_provider``) and its
hardened replacement (``degraded_control_hardening._private_local_provider``)
used ``provider.get("enabled", True)`` directly as a boolean predicate. A
provider carrying ``enabled: "false"`` (a string coming from JSON/YAML config)
is truthy in Python, so an explicitly disabled provider was admitted as a
delegation target. That is a fail-open on an explicit disable.

These tests are hermetic: no Neo4j, no network, no service restart, no live
state. They exercise only pure admission predicates.
"""

from __future__ import annotations

import pytest

from assistx.degraded_control_hardening import _private_local_provider
from assistx.degraded_control_plane import _is_local_provider

HELPERS = [
    pytest.param(_is_local_provider, id="base"),
    pytest.param(_private_local_provider, id="hardened"),
]

# Truthy non-bool values that the old ``bool(value)`` predicate admitted.
TRUTHY_NON_BOOL = [
    pytest.param("false", id="string-false"),
    pytest.param("no", id="string-no"),
    pytest.param("0", id="string-zero"),
    pytest.param("off", id="string-off"),
    pytest.param(1, id="int-one"),
    pytest.param(2, id="int-two"),
    pytest.param({"value": False}, id="mapping"),
    pytest.param(["x"], id="list"),
]


def _private_provider(**overrides):
    provider = {
        "enabled": True,
        "type": "lmstudio",
        "quota_class": "private",
        "access_urls": ["http://127.0.0.1:1234/v1"],
    }
    provider.update(overrides)
    return provider


@pytest.mark.parametrize("helper", HELPERS)
def test_real_bool_true_is_admitted(helper):
    assert helper(_private_provider()) is True


@pytest.mark.parametrize("helper", HELPERS)
def test_absent_enabled_keeps_legacy_default_true(helper):
    provider = _private_provider()
    provider.pop("enabled")
    assert helper(provider) is True


@pytest.mark.parametrize("helper", HELPERS)
def test_real_bool_false_is_denied(helper):
    assert helper(_private_provider(enabled=False)) is False


@pytest.mark.parametrize("helper", HELPERS)
@pytest.mark.parametrize("bad", TRUTHY_NON_BOOL)
def test_truthy_non_bool_enabled_is_denied(helper, bad):
    assert helper(_private_provider(enabled=bad)) is False
