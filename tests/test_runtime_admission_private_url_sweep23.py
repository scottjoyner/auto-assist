"""Hermetic regression tests for the runtime-admission private-URL fence.

`sweep23`: `assistx.runtime_admission._private_url` gates every
`live.access_paths[].base_url`. A dotless host that is entirely numeric is
IPv4 *integer notation* (``134744072`` is ``8.8.8.8``; ``0x08080808`` is the
same address). The base implementation returned ``True`` for any dotless host
before ever resolving it, so a public address written in integer/hex notation
was admitted as "private". These tests are pure string predicates: no socket,
HTTP, Redis, Neo4j, SQLite, container, credential or network mount is touched.
"""

from __future__ import annotations

import pytest

from assistx.runtime_admission import _private_url


@pytest.mark.parametrize(
    "url",
    [
        # 134744072 == 8.8.8.8, 184549376 == 11.0.0.0, 0x08080808 == 8.8.8.8.
        "http://134744072:1234/v1",
        "http://184549376:1234/v1",
        "http://0x08080808:1234/v1",
    ],
)
def test_public_ip_in_integer_notation_is_not_private(url: str) -> None:
    """A public IPv4 written as a bare integer/hex must not pass the fence."""
    assert _private_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        # 167772161 == 10.0.0.1, 2130706433 == 127.0.0.1,
        # 0x7f000001 == 127.0.0.1, 0x0a000001 == 10.0.0.1.
        "http://167772161:1234/v1",
        "http://2130706433:1234/v1",
        "http://0x7f000001:1234/v1",
        "http://0x0a000001:1234/v1",
    ],
)
def test_private_and_loopback_integer_notation_stays_private(url: str) -> None:
    """Controls: genuinely private/loopback integer notation is still admitted."""
    assert _private_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8:1234/v1",
        "https://1.1.1.1/v1",
    ],
)
def test_canonical_dotted_public_ip_stays_rejected(url: str) -> None:
    """Control: the canonical dotted spelling of a public IP is rejected."""
    assert _private_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1:1234/v1",
        "http://100.69.158.114:1234/v1",
        "http://localhost:1234/v1",
        "http://host.docker.internal:1234/v1",
        "http://api.lan:1234/v1",
        "http://node.ts.net:1234/v1",
        "http://myhost:1234/v1",
    ],
)
def test_private_and_single_label_hosts_stay_admitted(url: str) -> None:
    """Controls: ordinary private hosts and single-label names are unchanged."""
    assert _private_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "ftp://134744072:1234/v1",
        "not-a-url",
        "http://evil.example.com:1234/v1",
    ],
)
def test_non_private_or_malformed_inputs_are_rejected(url: str) -> None:
    """Controls: bad schemes, non-URLs and public DNS names stay rejected."""
    assert _private_url(url) is False
