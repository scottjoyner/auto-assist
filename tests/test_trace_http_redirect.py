"""Local real-HTTP security contract for token-bearing trace requests.

No network beyond loopback, no production credentials, no remote workers.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from assistx import fleet_node_agent


@contextmanager
def listener(*, redirect_to=None, code=302):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.do_POST()

        def do_POST(self):
            calls.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "node": self.headers.get("X-Fleet-Node-Token"),
                }
            )
            if redirect_to is not None:
                self.send_response(code)
                self.send_header("Location", redirect_to(self.server.server_port))
                self.end_headers()
            else:
                raw = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


@pytest.mark.parametrize("redirect_code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "path",
    [
        "/api/tasks/fixture/claim",
        "/api/tasks/fixture/heartbeat",
        "/api/fleet/trace-execution/claim-lease-proof",
        "/api/fleet/trace-execution/claim-current-status",
        "/api/tasks/fixture/complete",
    ],
)
def test_no_credential_leak_to_second_listener(redirect_code, path):
    with listener() as (destination, target_calls):
        with listener(
            code=redirect_code,
            redirect_to=lambda _: destination + "/would-receive-credentials",
        ) as (allowed, source_calls):
            status, _ = fleet_node_agent._trace_http(
                "POST",
                allowed + path,
                auth=("fixture-user", "fixture-password"),
                headers={"X-Fleet-Node-Token": "fixture-only-token"},
                data={"command_id": "probe.noop.v1"},
                timeout=3,
            )
            assert status == redirect_code
            assert len(source_calls) == 1
            assert source_calls[0]["node"] == "fixture-only-token"
            assert source_calls[0]["auth"].startswith("Basic ")
            assert target_calls == []


@pytest.mark.parametrize("redirect_code", [301, 302, 307, 308])
def test_no_same_origin_relative_redirect_followed(redirect_code):
    with listener(
        code=redirect_code,
        redirect_to=lambda _: "/replayed-request",
    ) as (source, calls):
        status, _ = fleet_node_agent._trace_http(
            "POST",
            source + "/api/tasks/fixture/claim",
            auth=("fixture-user", "fixture-password"),
            headers={"X-Fleet-Node-Token": "fixture-only-token"},
            data={"a": 1},
            timeout=3,
        )
        assert status == redirect_code
        assert [x["path"] for x in calls] == ["/api/tasks/fixture/claim"]


def test_non_trace_http_behavior_preserved():
    with listener() as (destination, target_calls):
        with listener(redirect_to=lambda _: destination + "/redirect-target") as (allowed, _):
            status, payload = fleet_node_agent._http(
                "POST",
                allowed + "/original",
                data={"command_id": "test"},
                timeout=3,
            )
            assert status == 200
            assert payload == {"ok": True}
            assert [x["path"] for x in target_calls] == ["/redirect-target"]


def test_trace_request_without_redirect_completes():
    with listener() as (allowed, calls):
        status, payload = fleet_node_agent._trace_http(
            "POST",
            allowed + "/api/fleet/trace-execution/claim-lease-proof",
            auth=("fixture-user", "fixture-password"),
            headers={"X-Fleet-Node-Token": "fixture-only-token"},
            data={"x": 1},
            timeout=3,
        )
        assert status == 200 and payload == {"ok": True}
        assert len(calls) == 1


def test_trace_capable_polling_rejects_redirect_before_claim(monkeypatch):
    with listener() as (destination, target_calls):
        with listener(
            code=302,
            redirect_to=lambda _: destination + "/stolen-listing",
        ) as (allowed, source_calls):
            monkeypatch.setenv("FLEET_TRACE_ISSUER_ORIGIN", allowed)
            found = fleet_node_agent._poll_once(
                assistx_url=allowed,
                router_url="unused",
                auth=("fixture-user", "fixture-password"),
                node_id="xwing",
                caps=["trace-probe"],
                lmstudio_url=None,
            )
            assert found is False
            assert len(source_calls) == 1
            assert target_calls == []


def test_trace_capable_poll_requires_pinned_origin(monkeypatch):
    monkeypatch.setenv("FLEET_TRACE_ISSUER_ORIGIN", "https://expected.example")
    monkeypatch.setattr(
        fleet_node_agent,
        "_http",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("network must not run")),
    )
    assert (
        fleet_node_agent._poll_once(
            assistx_url="https://unexpected.example",
            router_url="unused",
            auth=None,
            node_id="xwing",
            caps=["trace-probe"],
            lmstudio_url=None,
        )
        is False
    )
