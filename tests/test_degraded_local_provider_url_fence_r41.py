"""Hermetic fence test: a degraded-mode provider with no verifiable endpoint
must not be classified as a local provider (round 41).

No network, DB, Redis, Neo4j, service or credential access: the subject is a
pure predicate over an in-memory mapping.
"""

from assistx.degraded_control_plane import _is_local_provider


def _provider(**overrides):
    base = {
        "enabled": True,
        "quota_class": "local",
        "type": "lmstudio",
        "node_id": "x1-370",
        "base_url": "http://127.0.0.1:1234/v1",
    }
    base.update(overrides)
    return base


def test_provider_with_no_endpoint_url_is_not_local():
    provider = _provider()
    provider.pop("base_url")
    assert _is_local_provider(provider) is False


def test_provider_with_empty_access_urls_is_not_local():
    provider = _provider(access_urls=[])
    provider.pop("base_url")
    assert _is_local_provider(provider) is False


def test_provider_with_only_blank_urls_is_not_local():
    provider = _provider(access_urls=["", "   "], base_url="   ")
    assert _is_local_provider(provider) is False


def test_control_valid_local_endpoint_still_local():
    assert _is_local_provider(_provider()) is True
    assert _is_local_provider(
        _provider(access_urls=["http://100.81.57.77:1234/v1"])
    ) is True


def test_control_nonlocal_or_disabled_provider_still_rejected():
    assert _is_local_provider(_provider(quota_class="public")) is False
    assert _is_local_provider(_provider(enabled=False)) is False
