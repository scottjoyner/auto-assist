"""Hermetic regression for sweep25: empty URL lists must not be judged local.

All fixtures are synthetic dicts; no network, DB, Redis, container, service,
timer, credential file or live Assistant/AssistX state is read. Only the pure
``degraded_control_plane._is_local_provider`` predicate is exercised.
"""

from __future__ import annotations

from assistx.degraded_control_plane import _is_local_provider

_BASE = {
    "name": "ghost-local",
    "type": "lmstudio",
    "node_id": "ghost-node",
    "enabled": True,
    "quota_class": "local",
}


def _provider(**overrides: object) -> dict[str, object]:
    return {**_BASE, **overrides}


def test_missing_endpoint_is_not_local() -> None:
    """A provider with no URL at all cannot be verified as local."""

    assert _is_local_provider(_provider()) is False


def test_empty_access_urls_without_base_url_is_not_local() -> None:
    """An explicitly empty access_urls list must not pass vacuously."""

    assert _is_local_provider(_provider(access_urls=[])) is False


def test_blank_access_urls_are_not_local() -> None:
    """Empty/None URL entries must not be silently filtered away."""

    provider = _provider(access_urls=["", None])
    assert _is_local_provider(provider) is False


def test_blank_base_url_is_not_local() -> None:
    """An empty base_url fallback must not pass vacuously."""

    assert _is_local_provider(_provider(base_url="")) is False


def test_valid_local_url_still_local() -> None:
    """Control: a real loopback endpoint stays accepted."""

    provider = _provider(access_urls=["http://127.0.0.1:1234/v1"])
    assert _is_local_provider(provider) is True


def test_disabled_provider_still_rejected() -> None:
    """Control: an explicitly disabled provider stays rejected."""

    assert _is_local_provider(_provider(enabled=False, access_urls=["http://127.0.0.1:1234/v1"])) is False


def test_non_local_type_still_rejected() -> None:
    """Control: a non-local provider type stays rejected."""

    provider = _provider(type="hosted", access_urls=["http://127.0.0.1:1234/v1"])
    assert _is_local_provider(provider) is False


def test_hosted_quota_still_rejected() -> None:
    """Control: a hosted quota class stays rejected."""

    provider = _provider(quota_class="hosted", access_urls=["http://127.0.0.1:1234/v1"])
    assert _is_local_provider(provider) is False
