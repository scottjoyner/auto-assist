"""Offline-only proof that Router admin tokens never follow a redirect."""
from __future__ import annotations

import io
import urllib.request
from email.message import Message
from urllib.error import HTTPError
from urllib.request import BaseHandler, ProxyHandler, build_opener
from urllib.response import addinfourl

import pytest

from assistx import control_room


class SyntheticHTTP(BaseHandler):
    """Return in-memory responses; never open a socket or a real URL."""
    handler_order = 100

    def __init__(self, *, redirect=True, redirect_status=302):
        self.redirect = redirect
        self.redirect_status = redirect_status
        self.foreign_calls = 0
        self.seen_admin = []

    @staticmethod
    def response(body, headers, url, status):
        response = addinfourl(io.BytesIO(body), headers, url, code=status)
        response.msg = "Found" if status == 302 else "OK"
        return response

    def http_open(self, request):
        if request.host != "router.invalid:8088":
            self.foreign_calls += 1
            self.seen_admin.append(bool(request.get_header("X-admin-token")))
            return self.response(b'{"providers": []}', Message(), request.full_url, 200)
        if self.redirect:
            headers = Message()
            headers["Location"] = "http://foreign.invalid:9090/redirect-target"
            return self.response(b"", headers, request.full_url, self.redirect_status)
        return self.response(b'{"providers": []}', Message(), request.full_url, 200)


def wire_offline_http(monkeypatch, fake):
    # Before the source fix, urlopen uses urllib's normal global opener.
    monkeypatch.setattr(urllib.request, "_opener", build_opener(fake))
    # After the fix, test the isolated secure opener with the same fake handler.
    installed = []

    def secure_factory(*handlers):
        installed.extend(handlers)
        return build_opener(fake, *handlers)

    monkeypatch.setattr(control_room, "build_opener", secure_factory, raising=False)
    return installed


@pytest.mark.parametrize("redirect_status", [301, 302, 303, 307, 308])
def test_admin_redirect_rejected_without_forwarding_header(monkeypatch, redirect_status):
    fake = SyntheticHTTP(redirect=True, redirect_status=redirect_status)
    installed = wire_offline_http(monkeypatch, fake)
    with pytest.raises(HTTPError):
        control_room._http_json("http://router.invalid:8088", "/admin/providers", admin_token="synthetic-not-real")
    assert fake.foreign_calls == 0
    assert not fake.seen_admin

    # A credential-bearing path must not honor inherited OS proxy settings.
    assert any(isinstance(h, ProxyHandler) and h.proxies == {} for h in installed)


def test_admin_direct_response_remains_compatible(monkeypatch):
    fake = SyntheticHTTP(redirect=False)
    installed = wire_offline_http(monkeypatch, fake)
    body, _ = control_room._http_json(
        "http://router.invalid:8088", "/admin/providers", admin_token="synthetic-not-real"
    )
    assert body == {"providers": []}
    assert fake.foreign_calls == 0
    assert installed


def test_public_probe_redirect_is_not_changed(monkeypatch):
    fake = SyntheticHTTP(redirect=True)
    installed = wire_offline_http(monkeypatch, fake)
    body, _ = control_room._http_json("http://router.invalid:8088", "/health")
    assert body == {"providers": []}
    assert fake.foreign_calls == 1
    assert fake.seen_admin == [False]
    assert installed == []
