"""Read-only verifier must not redirect or proxy Basic/admin credentials."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/verify-runtime-projection.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("projection_verifier_transport", SOURCE)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    return verifier


def test_authenticated_verifier_uses_nonproxy_nonredirect_opener(monkeypatch):
    verifier = load_verifier()
    installed = []
    requests = []

    class FakeOpener:
        def open(self, req, timeout):
            requests.append((req.full_url, bool(req.get_header("X-admin-token")), timeout))
            return io.BytesIO(b'{"status": "synthetic"}')
    def factory(*handlers):
        installed.extend(handlers)
        return FakeOpener()

    monkeypatch.setattr(verifier.urllib.request, "build_opener", factory)
    monkeypatch.setattr(
        verifier.urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("global redirect-following urlopen must not run"),
    )
    result = verifier._get_json(
        "http://router.invalid:8088/admin/runtime-projection",
        {"X-Admin-Token": "synthetic-not-real"},
    )
    assert result == {"status": "synthetic"}
    assert requests == [("http://router.invalid:8088/admin/runtime-projection", True, 15)]
    assert any(isinstance(h, ProxyHandler) and h.proxies == {} for h in installed)
    rejects = [h for h in installed if isinstance(h, HTTPRedirectHandler)]
    assert len(rejects) == 1
    for status in (301, 302, 303, 307, 308):
        req = Request("http://router.invalid:8088/admin/runtime-projection")
        assert rejects[0].redirect_request(
            req, None, status, "synthetic redirect", {}, "http://foreign.invalid/receive"
        ) is None


def test_verifier_basic_auth_also_uses_same_restrictions(monkeypatch):
    verifier = load_verifier()
    handlers_seen = []

    class FakeOpener:
        def open(self, request, timeout):
            assert bool(request.get_header("Authorization"))
            return io.BytesIO(b'{"status": "synthetic"}')

    monkeypatch.setattr(verifier.urllib.request, "build_opener",
                        lambda *handlers: (handlers_seen.extend(handlers), FakeOpener())[1])
    monkeypatch.setattr(verifier.urllib.request, "urlopen",
                        lambda *_a, **_k: pytest.fail("global opener must not handle Basic auth"))
    assert verifier._get_json(
        "http://assistx.invalid/api/router/runtime-projection",
        {"Authorization": "Basic c3ludGhldGlj"},
    ) == {"status": "synthetic"}
    assert any(isinstance(h, ProxyHandler) and h.proxies == {} for h in handlers_seen)
    assert any(isinstance(h, HTTPRedirectHandler) for h in handlers_seen)

def test_verifier_http_error_never_reads_or_echoes_body_url_or_headers(monkeypatch):
    import urllib.error

    verifier = load_verifier()
    sentinel = "SYNTHETIC_SECRET_NEVER_PRINT"
    request_url = "http://identity:secret@router.invalid:8088/admin/runtime-projection"
    body_read = []

    class UntrustedBody(io.BytesIO):
        def read(self, *args, **kwargs):
            body_read.append(True)
            return super().read(*args, **kwargs)

    class FakeOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 403, sentinel, {},
                UntrustedBody((sentinel * 50).encode()),
            )

    monkeypatch.setattr(verifier.urllib.request, "build_opener",
                        lambda *_handlers: FakeOpener())
    with pytest.raises(RuntimeError) as err:
        verifier._get_json(request_url, {"X-Admin-Token": sentinel})
    text = str(err.value)
    assert "403" in text
    assert sentinel not in text
    assert "secret@" not in text
    assert "router.invalid" not in text
    assert body_read == [], "never read untrusted error response body"
    assert err.value.__suppress_context__ is True


@pytest.mark.parametrize("error_factory", [
    lambda secret: OSError("faked request failed: " + secret),
    lambda secret: ValueError("faked decoder failed: " + secret),
])
def test_verifier_other_failure_never_echoes_untrusted_details(monkeypatch, error_factory):
    verifier = load_verifier()
    sentinel = "SYNTHETIC_SECRET_NEVER_PRINT"

    class FakeOpener:
        def open(self, request, timeout):
            raise error_factory(sentinel)

    monkeypatch.setattr(verifier.urllib.request, "build_opener",
                        lambda *_handlers: FakeOpener())
    with pytest.raises(RuntimeError) as err:
        verifier._get_json("http://router.invalid/hidden", {"Authorization": sentinel})
    assert sentinel not in str(err.value)
    assert "router.invalid" not in str(err.value)
    assert err.value.__suppress_context__ is True
