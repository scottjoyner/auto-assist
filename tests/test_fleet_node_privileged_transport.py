"""Pure-stdlib, socketless regressions for privileged fleet-agent transport."""
from __future__ import annotations

import importlib.util
import io
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request


def _load_agent():
    root = Path(__file__).resolve().parents[1] / "src" / "assistx"
    pkg = types.ModuleType("assistx")
    pkg.__path__ = [str(root)]
    sys.modules["assistx"] = pkg
    dependencies = {
        "assistx.kv_cache": {"runtime_capabilities": lambda *_a, **_k: {}},
        "assistx.lm_studio_client_auth": {
            "headers_for_request": lambda _url, _auth, existing: dict(existing or {})
        },
        "assistx.recovery_runbooks": {
            "RecoveryRunbookExecutor": type("NoOpRecovery", (), {})
        },
    }
    for name, entries in dependencies.items():
        module = types.ModuleType(name)
        for key, value in entries.items():
            setattr(module, key, value)
        sys.modules[name] = module
    spec = importlib.util.spec_from_file_location("assistx.fleet_node_agent", root / "fleet_node_agent.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

AGENT = _load_agent()

class Response:
    def __init__(self, data=b"{}", status=200):
        self.data = data
        self.status = status
        self.last_limit = None
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, limit=None):
        self.last_limit = limit
        return self.data if limit is None else self.data[:limit]

class Opener:
    def __init__(self, response):
        self.response = response
        self.requests = []
    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        return self.response

class PrivilegedTransportSafety(unittest.TestCase):
    def test_default_urllib_would_forward_custom_basic_and_node_headers(self):
        req = Request("http://trusted.invalid/api/agent/tasks", headers={
            "Authorization": "Basic SYNTHETIC",
            "X-Fleet-Node-Token": "SYNTHETIC",
        })
        redirected = HTTPRedirectHandler().redirect_request(
            req, None, 302, "found", {}, "http://other.invalid/reception"
        )
        self.assertIn("Authorization", redirected.headers)
        self.assertIn("X-fleet-node-token", redirected.headers)

    def test_hardened_handler_rejects_every_redirect_even_to_same_origin(self):
        req = Request("http://trusted.invalid/api/agent/tasks",
                      headers={"Authorization": "Basic SYNTHETIC"})
        handler = AGENT._RejectPrivilegedRedirect()
        for status in (301, 302, 303, 307, 308):
            self.assertIsNone(handler.redirect_request(
                req, None, status, "redirect", {}, "http://other.invalid/capture"
            ))

    def test_basic_auth_uses_no_proxy_and_no_redirect(self):
        response = Response()
        opened = Opener(response)
        captured = []
        def build(*handlers):
            captured.extend(handlers)
            return opened
        with patch.object(AGENT.urllib.request, "build_opener", build), patch.object(
            AGENT.urllib.request, "urlopen", side_effect=AssertionError("urlopen bypass")
        ):
            status, body = AGENT._http(
                "GET", "http://trusted.invalid/api/agent/tasks",
                auth=("operator-synthetic", "password-synthetic"), timeout=4
            )
        self.assertEqual((status, body), (200, {}))
        self.assertEqual(response.last_limit, 1024 * 1024 + 1)
        self.assertEqual(len(opened.requests), 1)
        req, timeout = opened.requests[0]
        self.assertTrue(req.get_header("Authorization").startswith("Basic "))
        self.assertEqual(timeout, 4)
        self.assertEqual(captured[0].proxies, {})
        self.assertIsInstance(captured[1], AGENT._RejectPrivilegedRedirect)

    def test_node_token_or_bearer_also_uses_secure_opener(self):
        for name, value in (("X-Fleet-Node-Token", "SYNTHETIC"),
                            ("Authorization", "Bearer SYNTHETIC"),
                            ("X-Api-Key", "SYNTHETIC")):
            with self.subTest(header=name):
                opener = Opener(Response())
                with patch.object(AGENT.urllib.request, "build_opener", return_value=opener), patch.object(
                    AGENT.urllib.request, "urlopen", side_effect=AssertionError("urlopen bypass")
                ):
                    self.assertEqual(AGENT._http(
                        "GET", "http://trusted.invalid/api/agent/tasks",
                        headers={name: value}
                    )[0], 200)
                self.assertEqual(len(opener.requests), 1)

    def test_nonprivileged_requests_preserve_existing_transport(self):
        response = Response()
        with patch.object(AGENT.urllib.request, "urlopen", return_value=response) as urlopen, patch.object(
            AGENT.urllib.request, "build_opener", side_effect=AssertionError("opener not needed")
        ):
            result = AGENT._http("GET", "http://localhost:1234/v1/models")
        self.assertEqual(result, (200, {}))
        self.assertIsNone(response.last_limit)
        urlopen.assert_called_once()

    def test_oversized_privileged_response_is_rejected_before_unbounded_read(self):
        response = Response(b"a" * (1024 * 1024 + 1))
        with patch.object(AGENT.urllib.request, "build_opener", return_value=Opener(response)):
            result = AGENT._http("GET", "http://trusted.invalid/agent",
                                 headers={"X-Fleet-Node-Token": "SYNTHETIC"})
        self.assertEqual(result, (0, {"detail": "privileged_response_oversized"}))
        self.assertEqual(response.last_limit, 1024 * 1024 + 1)

    def test_http_error_body_limited_and_closed(self):
        too_long = io.BytesIO(b"a" * 65537)
        err = HTTPError("http://trusted.invalid/x", 403, "denied", {}, too_long)
        with patch.object(AGENT.urllib.request, "build_opener", return_value=Opener(Response())), patch.object(
            AGENT.urllib.request, "urlopen", side_effect=err
        ):
            # Unprivileged path uses urlopen and still bounds error bodies.
            result = AGENT._http("GET", "http://trusted.invalid/x")
        self.assertEqual(result, (403, {"detail": "http_error_body_oversized"}))
        self.assertTrue(too_long.closed)

    def test_url_errors_never_echo_exception_payload(self):
        with patch.object(AGENT.urllib.request, "build_opener",
                          side_effect=URLError("SYNTHETIC_SECRET_DO_NOT_ECHO")):
            code, body = AGENT._http("GET", "http://trusted.invalid/x",
                                     auth=("synthetic", "synthetic"))
        self.assertEqual(code, 0)
        self.assertEqual(body, {"detail": "URLError"})
        self.assertNotIn("SYNTHETIC_SECRET", str(body))

if __name__ == "__main__":
    unittest.main()
