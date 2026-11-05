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
    """Import a standalone worker without poisoning other pytest modules."""
    root = Path(__file__).resolve().parents[1] / "src" / "assistx"
    package_names = (
        "assistx", "assistx.kv_cache", "assistx.lm_studio_client_auth",
        "assistx.recovery_runbooks", "assistx.fleet_node_agent",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in package_names}
    try:
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
        spec = importlib.util.spec_from_file_location(
            "assistx.fleet_node_agent", root / "fleet_node_agent.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in previous.items():
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


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
    def test_isolated_loader_does_not_mutate_global_assistx_module_registry(self):
        names = (
            "assistx", "assistx.kv_cache", "assistx.lm_studio_client_auth",
            "assistx.recovery_runbooks", "assistx.fleet_node_agent",
        )
        before = {name: sys.modules.get(name) for name in names}
        second = _load_agent()
        self.assertTrue(hasattr(second, "_http"))
        self.assertTrue(all(sys.modules.get(name) is previous for name, previous in before.items()))

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


class IdentityOnlyProbeSafety(unittest.TestCase):
    def _registered(self):
        return patch.dict(AGENT.os.environ, {
            "FLEET_NODE_ID": "node-synthetic",
            "FLEET_NODE_TOKEN": "SYNTHETIC_NODE_TOKEN",
            "FLEET_NODE_IDENTITY_APPROVED_ORIGIN": "https://assistx.example.invalid",
        })

    def test_https_positive_identity_stays_hold_without_task_calls(self):
        calls = []
        def fake_http(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return 200, {
                "machine_identity_verified": True,
                "node_id": "node-synthetic",
                "task_admission": "HOLD",
                "legacy_task_authorized": False,
            }
        with self._registered(), patch.object(AGENT, "_http", side_effect=fake_http):
            result = AGENT.probe_node_identity(
                assistx_url="https://assistx.example.invalid", node_id="node-synthetic"
            )
        self.assertTrue(result["identity_verified"])
        self.assertFalse(result["production_admission_permitted"])
        self.assertFalse(result["legacy_task_authorized"])
        self.assertEqual(result["task_admission"], "HOLD")
        self.assertEqual(len(calls), 1)
        method, url, options = calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "https://assistx.example.invalid/api/fleet-node/identity")
        self.assertIsNone(options["auth"])
        self.assertEqual(options["headers"]["X-Fleet-Node-Id"], "node-synthetic")
        self.assertEqual(options["headers"]["X-Fleet-Node-Token"], "SYNTHETIC_NODE_TOKEN")
        self.assertNotIn("/api/agent/tasks", url)
        self.assertNotIn("/claim", url)

    def test_unapproved_hosts_and_urls_are_rejected_before_token_transfer(self):
        bad = (
            "http://assistx.example.invalid:8000", "http://10.2.0.1:8000",
            "https://operator:secret@assistx.example.invalid",
            "https://assistx.example.invalid/not-root",
            "https://assistx.example.invalid?q=something",
            "https://assistx.example.invalid/#fragment",
            "https://assistx.example.invalid:invalid",
            "file:///etc/passwd",
        )
        with self._registered(), patch.object(AGENT, "_http", side_effect=AssertionError("never send token")):
            for url in bad:
                with self.subTest(url=url):
                    result = AGENT.probe_node_identity(
                        assistx_url=url, node_id="node-synthetic"
                    )
                    self.assertFalse(result["identity_verified"])
                    self.assertEqual(result["task_admission"], "HOLD")

    def test_loopback_requires_exact_ip_literal(self):
        with self._registered(), patch.dict(AGENT.os.environ, {
            "FLEET_NODE_IDENTITY_APPROVED_ORIGIN": "http://127.0.0.1:8000",
        }), patch.object(AGENT, "_http", return_value=(404, {})) as fake_http:
            accepted = AGENT.probe_node_identity(
                assistx_url="http://127.0.0.1:8000", node_id="node-synthetic"
            )
            self.assertFalse(accepted["identity_verified"])
            fake_http.assert_called_once()
        with self._registered(), patch.object(AGENT, "_http", side_effect=AssertionError("localhost not pinned")):
            refused = AGENT.probe_node_identity(
                assistx_url="http://localhost:8000", node_id="node-synthetic"
            )
            self.assertFalse(refused["identity_verified"])

    def test_node_mismatch_and_missing_token_never_call_server(self):
        with self._registered(), patch.object(AGENT, "_http", side_effect=AssertionError("no call allowed")):
            result = AGENT.probe_node_identity(
                assistx_url="https://assistx.example.invalid", node_id="other"
            )
            self.assertFalse(result["identity_verified"])
        with patch.dict(AGENT.os.environ, {
            "FLEET_NODE_ID": "node-synthetic", "FLEET_NODE_TOKEN": "",
        }), patch.object(AGENT, "_http", side_effect=AssertionError("no token")):
            result = AGENT.probe_node_identity(
                assistx_url="https://assistx.example.invalid", node_id="node-synthetic"
            )
            self.assertFalse(result["identity_verified"])

    def test_wrong_or_absent_pinned_identity_origin_denied_before_token_use(self):
        with self._registered(), patch.object(AGENT, "_http", side_effect=AssertionError("no outbound token")):
            with patch.dict(AGENT.os.environ, {
                "FLEET_NODE_IDENTITY_APPROVED_ORIGIN": "https://other.example.invalid",
            }):
                result = AGENT.probe_node_identity(
                    assistx_url="https://assistx.example.invalid", node_id="node-synthetic"
                )
                self.assertFalse(result["identity_verified"])
                self.assertEqual(result["reason"], "IDENTITY_ORIGIN_NOT_PINNED")
            with patch.dict(AGENT.os.environ, {
                "FLEET_NODE_IDENTITY_APPROVED_ORIGIN": "",
            }):
                result = AGENT.probe_node_identity(
                    assistx_url="https://assistx.example.invalid", node_id="node-synthetic"
                )
                self.assertFalse(result["identity_verified"])
                self.assertEqual(result["task_admission"], "HOLD")

    def test_server_claiming_task_authority_denies_even_with_valid_identity(self):
        with self._registered(), patch.object(AGENT, "_http", return_value=(200, {
            "machine_identity_verified": True,
            "node_id": "node-synthetic",
            "task_admission": "READY",
            "legacy_task_authorized": True,
        })):
            result = AGENT.probe_node_identity(
                assistx_url="https://assistx.example.invalid", node_id="node-synthetic"
            )
        self.assertFalse(result["identity_verified"])
        self.assertEqual(result["reason"], "IDENTITY_NOT_VERIFIED")
        self.assertFalse(result["production_admission_permitted"])

    def test_cli_exits_nonzero_and_does_not_start_agent_loop(self):
        with self._registered(), patch.object(AGENT, "probe_node_identity", return_value={
            "identity_verified": True, "task_admission": "HOLD"
        }) as probe, patch.object(AGENT, "run", side_effect=AssertionError("no worker loop")), patch(
            "sys.argv", ["fleet_node_agent", "--identity-probe-only",
                         "--assistx-url", "https://assistx.example.invalid"]
        ), patch("builtins.print"):
            with self.assertRaises(SystemExit) as exit_ctx:
                AGENT.main()
        self.assertEqual(exit_ctx.exception.code, 2)
        probe.assert_called_once()

if __name__ == "__main__":
    unittest.main()
