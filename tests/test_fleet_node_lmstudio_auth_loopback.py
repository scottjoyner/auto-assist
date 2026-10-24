"""Real loopback HTTP bearer integration for the AssistX fleet node agent.

Synthetic server on an ephemeral port, intercepted test requests only.
The live LM Studio model server at port 1234 is never accessed.
"""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
import urllib.parse
import urllib.request

import pytest
from assistx import fleet_node_agent
from assistx.lm_studio_client_auth import LMStudioAuthHold

GOOD = "synthetic-allowed-agent-20261024"
BAD = "synthetic-denied-agent-20261024"


class SyntheticHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def _request(self, method):
        authorized = self.headers.get("Authorization") == "Bearer " + self.server.current_token
        if method == "POST":
            n = int(self.headers.get("Content-Length", "0"))
            if n > 4096 or self.path != "/v1/chat/completions":
                self.send_error(400)
                return
            self.rfile.read(n)
        if not authorized:
            status, data = 401, {"error": "unauthorized"}
        elif method == "POST":
            status, data = 200, {"choices": [{"message": {"content": "synthetic-only"}}]}
        else:
            status, data = 200, {"data": [{"id": "synthetic"}]}
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._request("GET")
    def do_POST(self):
        self._request("POST")


@pytest.fixture
def auth_server(monkeypatch, tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticHandler)
    server.current_token = GOOD
    loop = threading.Thread(target=server.serve_forever, daemon=True)
    loop.start()
    actual_urlopen = urllib.request.urlopen
    token = tmp_path / "fake-bearer"
    token.write_text(GOOD)
    token.chmod(0o600)
    monkeypatch.setenv("FLEET_LM_AUTH_POLICY", "required")
    monkeypatch.setenv("FLEET_LM_TOKEN_FILE", str(token))
    requests = []

    def redirect(request, timeout):
        target = urllib.parse.urlsplit(request.full_url)
        assert target.scheme == "http"
        assert (target.hostname, target.port) == ("127.0.0.1", 1234)
        assert target.path in ("/v1/models", "/v1/chat/completions")
        requests.append(request.get_method())
        redirected = urllib.request.Request(
            "http://127.0.0.1:" + str(server.server_port) + target.path,
            data=request.data, headers=dict(request.headers),
            method=request.get_method(),
        )
        return actual_urlopen(redirected, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", redirect)
    try:
        yield server, token, requests
    finally:
        server.shutdown()
        server.server_close()
        loop.join(timeout=3)


def test_catalog_valid_bearer_returns_model(auth_server):
    _, _, reqs = auth_server
    code, body = fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")
    assert code == 200 and body["data"][0]["id"] == "synthetic"
    assert reqs == ["GET"]


def test_chat_positive_synthetic_only(auth_server):
    _, _, reqs = auth_server
    code, body = fleet_node_agent._http(
        "POST", "http://127.0.0.1:1234/v1/chat/completions",
        data={"model": "synthetic", "messages": []},
    )
    assert code == 200 and body["choices"][0]["message"]["content"] == "synthetic-only"
    assert reqs == ["POST"]


def test_wrong_bearer_is_denied_without_anonymous_retry(auth_server):
    _, token, reqs = auth_server
    token.write_text(BAD)
    code, body = fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")
    assert code == 401 and body["error"] == "unauthorized"
    assert reqs == ["GET"]


def test_revoked_bearer_is_denied(auth_server):
    server, _, reqs = auth_server
    server.current_token = BAD
    code, body = fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")
    assert code == 401 and body["error"] == "unauthorized"
    assert reqs == ["GET"]


def test_missing_credential_refuses_before_http(auth_server, monkeypatch):
    _, _, reqs = auth_server
    monkeypatch.delenv("FLEET_LM_TOKEN_FILE")
    with pytest.raises(LMStudioAuthHold):
        fleet_node_agent._http("GET", "http://127.0.0.1:1234/v1/models")
    assert reqs == []
