"""Offline credential forwarding regressions for privileged router metadata.

No external sockets, production settings, credentials, or Task graph touched.
"""
from __future__ import annotations

import io
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

import pytest

from assistx import control_room


@pytest.mark.parametrize(
    "scheme_and_port",
    [
        "http://router.synthetic:8088",
        "https://router.synthetic:8443",
    ],
)
def test_admin_client_ignores_inherited_proxy_env(monkeypatch, scheme_and_port):
    """The actual opener factory must have an empty proxy map with a token."""
    monkeypatch.setenv("HTTP_PROXY", "http://synthetic-http-proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://synthetic-https-proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "socks5://synthetic-global-proxy.invalid:1080")
    seen = []

    class FakeOpener:
        def open(self, request, timeout):
            seen.append((request, timeout))
            return io.BytesIO(b'{"providers":[]}')
    def fake_build_opener(*handlers):
        # Real build_opener normally auto-installs environment-derived proxy
        # settings unless an explicit ProxyHandler is supplied.
        # Explicit empty ProxyHandler prevents the default proxy handler
        # from being installed. urllib intentionally drops an empty handler
        # from its installed handlers list.
        supplied = [h for h in handlers if isinstance(h, ProxyHandler)]
        assert len(supplied) == 1 and supplied[0].proxies == {}
        real = build_opener(*handlers)
        assert not any(isinstance(h, ProxyHandler) for h in real.handlers)
        assert any(isinstance(h, HTTPRedirectHandler) for h in handlers)
        return FakeOpener()

    monkeypatch.setattr(control_room, "build_opener", fake_build_opener)
    result, ms = control_room._http_json(
        scheme_and_port,
        "/admin/providers/effective",
        admin_token="synthetic-scoped-token",
    )
    assert result == {"providers": []}
    assert isinstance(ms, float)
    assert len(seen) == 1
    request, timeout = seen[0]
    assert request.get_header("X-admin-token") == "synthetic-scoped-token"
    assert timeout == 2.0
    assert request.full_url == scheme_and_port + "/admin/providers/effective"


def test_admin_client_denies_all_redirects_and_never_prints_token(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://synthetic-env-proxy.invalid:3128")
    inspected = []

    class FakeOpener:
        def open(self, request, timeout):
            assert request.get_header("X-admin-token") == "synthetic-secret"
            raise OSError("synthetic transport refused")

    def fake_build_opener(*handlers):
        inspected.extend(handlers)
        return FakeOpener()

    monkeypatch.setattr(control_room, "build_opener", fake_build_opener)
    with pytest.raises(OSError, match="synthetic transport refused"):
        control_room._http_json(
            "http://router.synthetic:8088", "/admin/admission",
            admin_token="synthetic-secret",
        )
    proxies = [h for h in inspected if isinstance(h, ProxyHandler)]
    assert len(proxies) == 1 and proxies[0].proxies == {}
    redirects = [h for h in inspected if isinstance(h, HTTPRedirectHandler)]
    assert len(redirects) == 1
    assert redirects[0].redirect_request(
        None, None, 302, "Moved", {},
        "http://malicious-proxy.invalid/capture",
    ) is None
