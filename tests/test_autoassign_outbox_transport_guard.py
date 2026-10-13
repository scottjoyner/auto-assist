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
        "https://example.invalid:99999",
        "https://example.invalid:invalid",
        "https://good.example.invalid\t.evil.invalid",
    ],
)
@pytest.mark.parametrize("authenticated", [True, False])
def test_credential_delivery_denied_before_network(monkeypatch, base_url, authenticated):
    monkeypatch.setattr(subject, "build_opener", _never_network)
    assert _synthetic_client(base_url, authenticated=authenticated)._deliver(_event()) is False


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

    def build(*handlers):
        assert isinstance(handlers[0], subject._RejectOutboxRedirects)
        if base_url.startswith("http://"):
            assert any(
                isinstance(handler, subject.ProxyHandler)
                and handler.proxies == {}
                for handler in handlers[1:]
            ), "loopback request must not inherit HTTP_PROXY"
        return OfflineOpener()

    monkeypatch.setattr(subject, "build_opener", build)
    assert _synthetic_client(base_url)._deliver(_event()) is True
    assert len(intercepts) == 1


def test_authorized_redirect_handler_disallows_all_redirects():
    handler = subject._RejectOutboxRedirects()
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
    assert _synthetic_client("https://assign.example.invalid")._deliver(_event()) is False


def test_insecure_local_hostname_is_not_treated_as_loopback():
    assert subject._outbox_destination_permitted(
        "http://host.docker.internal:8090/api/events"
    ) is False


def test_loopback_without_credentials_uses_nonredirecting_opener(monkeypatch):
    calls = []

    class OfflineOpener:
        def open(self, req, *, timeout):
            assert isinstance(req, Request)
            assert req.get_header("Authorization") is None
            calls.append(req.full_url)
            return io.BytesIO(b'{"accepted":true}')

    def build(*handlers):
        assert isinstance(handlers[0], subject._RejectOutboxRedirects)
        assert any(
            isinstance(handler, subject.ProxyHandler)
            and handler.proxies == {}
            for handler in handlers[1:]
        ), "even unauthenticated local HTTP must not use an external proxy"
        return OfflineOpener()

    monkeypatch.setattr(subject, "build_opener", build)
    assert _synthetic_client("http://127.0.0.1:8090", authenticated=False)._deliver(
        _event()
    ) is True
    assert len(calls) == 1


def test_denied_transport_keeps_event_undelivered_in_outbox(monkeypatch, tmp_path):
    monkeypatch.setattr(subject, "build_opener", _never_network)
    client = subject.OutboxClient(
        db_path=str(tmp_path / "synthetic-outbox.sqlite"),
        api_url="http://10.0.0.2:8090",
        api_user="synthetic-user",
        api_pass="synthetic-password-not-real",
        auto_flush=False,
    )
    client.enqueue({"event_id": "synthetic-undelivered"})
    assert client.flush(max_attempts=3) == 0
    stats = client.get_stats()
    assert stats.get("pending") == 1
    assert stats.get("delivered", 0) == 0
    pending = client.get_pending()
    assert len(pending) == 1
    assert pending[0].event_id == "synthetic-undelivered"
    assert pending[0].attempt_count == 1


@pytest.mark.parametrize(
    "body",
    [
        b'{"accepted":"true"}',
        b'{"accepted":1}',
        b'{"accepted":0}',
        b'{"accepted":false}',
        b'{"accepted":null}',
        b'[]',
        b'null',
    ],
)
def test_ambiguous_receipt_cannot_mark_event_delivered(monkeypatch, body):
    class OfflineOpener:
        def open(self, req, *, timeout):
            return io.BytesIO(body)

    monkeypatch.setattr(subject, "build_opener", lambda *_: OfflineOpener())
    assert _synthetic_client("https://assign.example.invalid")._deliver(_event()) is False
