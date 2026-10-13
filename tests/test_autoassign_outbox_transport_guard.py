"""No-network security tests for the Auto-Assign outbox transport boundary.

All requests are mocked. No real credentials, DNS, sockets or service calls.
"""
from __future__ import annotations

import io
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from assistx import outbox_client as subject


def _synthetic_client(url: str, *, authenticated: bool = True):
    client = subject.OutboxClient.__new__(subject.OutboxClient)
    client.api_url = url
    client._auth_header = "Basic synthetic-not-real" if authenticated else None
    return client


def _event():
    return subject.OutboxEntry(
        outbox_id="synthetic-id",
        event_id="synthetic-event",
        payload_json='{"event_id":"synthetic-event"}',
    )


def _never_network(*args, **kwargs):
    raise AssertionError("network operation must never occur")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://10.0.0.2:8090",
        "http://172.20.0.2:8090",
        "http://192.0.2.5:8090",
        "http://100.90.30.2:8090",
        "http://assign.local:8090",
        "ftp://service.example.invalid",
        "https://user:synthetic@example.invalid",
        "http://localhost:8090/?unexpected=1",
    ],
)
def test_credential_delivery_denied_before_network(monkeypatch, base_url):
    monkeypatch.setattr(subject, "urlopen", _never_network)
    monkeypatch.setattr(subject, "build_opener", _never_network)
    assert _synthetic_client(base_url)._deliver(_event()) is False


@pytest.mark.parametrize(
    "base_url",
    ["https://assign.example.invalid:443", "http://127.0.0.1:8090", "http://[::1]:8090"],
)
def test_accepted_transport_uses_nonredirecting_opener(monkeypatch, base_url):
    intercepts = []

    class OfflineOpener:
        def open(self, req, *, timeout):
            assert timeout == 15
            assert req.get_header("Authorization") == "Basic synthetic-not-real"
            intercepts.append(req.full_url)
            return io.BytesIO(b'{"accepted":true}')

    def build(handler):
        assert isinstance(handler, subject._NoAuthorizationRedirects)
        return OfflineOpener()

    monkeypatch.setattr(subject, "build_opener", build)
    monkeypatch.setattr(subject, "urlopen", _never_network)
    assert _synthetic_client(base_url)._deliver(_event()) is True
    assert len(intercepts) == 1


def test_authorized_redirect_handler_disallows_all_redirects():
    handler = subject._NoAuthorizationRedirects()
    req = Request("https://assign.example.invalid/api/events", method="POST")
    result = handler.redirect_request(
        req, None, 302, "synthetic redirect", {}, "http://other.invalid",
    )
    assert result is None


def test_rejected_redirect_retries_as_nondelivery(monkeypatch):
    class RedirectOpener:
        def open(self, req, *, timeout):
            raise HTTPError(req.full_url, 302, "redirect denied", {}, None)

    monkeypatch.setattr(subject, "build_opener", lambda *_: RedirectOpener())
    monkeypatch.setattr(subject, "urlopen", _never_network)
    assert _synthetic_client("https://assign.example.invalid")._deliver(_event()) is False


def test_insecure_local_hostname_is_not_treated_as_loopback():
    assert subject._authenticated_transport_permitted(
        "http://host.docker.internal:8090/api/events"
    ) is False


def test_no_credentials_uses_previous_unauthenticated_client_path(monkeypatch):
    calls = []

    def offline(req, timeout):
        calls.append(req.full_url)
        return io.BytesIO(b'{"accepted":true}')

    monkeypatch.setattr(subject, "urlopen", offline)
    monkeypatch.setattr(subject, "build_opener", _never_network)
    assert _synthetic_client("http://127.0.0.1:8090", authenticated=False)._deliver(
        _event()
    ) is True
    assert len(calls) == 1
