"""Synthetics: legacy docs/coordination probes must authenticate or deny."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import router_admin_context_readonly as client


class FakeResponse:
    def __init__(self, status=200):
        self.status = status
        self.body_read = False

    def read(self):
        self.body_read = True
        raise AssertionError("Private context body read prohibited")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, status=200):
        self.status = status
        self.request = None

    def open(self, req, **kw):
        self.request = req
        if self.status in (302, 401, 403, 500):
            raise HTTPError(req.full_url, self.status, "no body", None, None)
        return FakeResponse(self.status)


class ClientFirstContracts(unittest.TestCase):
    def test_no_token_never_sends_anonymous_admin_get(self):
        with patch.dict(os.environ, {"AUTO_ROUTER_ADMIN_TOKEN": ""}), patch.object(client.request, "build_opener") as opener:
            result = client.probe_admin_context()
        opener.assert_not_called()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "admin_token_missing")
        self.assertIsNone(result["has_context"])

    def test_authenticated_status_200_no_sensitive_body_read(self):
        opener = FakeOpener(200)
        with patch.dict(os.environ, {"AUTO_ROUTER_ADMIN_TOKEN": "SYNTHETIC_PRIVATE_TOKEN"}), patch.object(client.request, "build_opener", return_value=opener) as mk:
            result = client.probe_admin_context()
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(opener.request.full_url, "http://127.0.0.1:8088/admin/context")
        self.assertEqual(opener.request.get_header("X-admin-token"), "SYNTHETIC_PRIVATE_TOKEN")
        self.assertNotIn("SYNTHETIC_PRIVATE_TOKEN", str(result))
        self.assertIsNone(result["has_context"])
        self.assertEqual(len(mk.call_args.args), 2)
        self.assertEqual(mk.call_args.args[0].proxies, {})
        self.assertIsInstance(mk.call_args.args[1], client._RejectRedirect)

    def test_denied_auth_401_403_are_not_healthy(self):
        for status in (401, 403):
            with self.subTest(status=status), patch.dict(os.environ, {"AUTO_ROUTER_ADMIN_TOKEN": "fake"}), patch.object(client.request, "build_opener", return_value=FakeOpener(status)):
                result = client.probe_admin_context()
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["http_status"], status)

    def test_redirect_302_is_never_followed(self):
        with patch.dict(os.environ, {"AUTO_ROUTER_ADMIN_TOKEN": "fake"}), patch.object(client.request, "build_opener", return_value=FakeOpener(302)):
            result = client.probe_admin_context()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["http_status"], 302)

    def test_transport_unavailable_never_logs_credentials(self):
        class BrokenOpener:
            def open(self, *args, **kwargs):
                raise URLError("PRIVATE_TOKEN_CANARY")
        with patch.dict(os.environ, {"AUTO_ROUTER_ADMIN_TOKEN": "PRIVATE_TOKEN_CANARY"}), patch.object(client.request, "build_opener", return_value=BrokenOpener()):
            result = client.probe_admin_context()
        self.assertEqual(result["status"], "unreachable")
        self.assertNotIn("PRIVATE_TOKEN_CANARY", str(result))

    def test_legacy_scripts_no_longer_send_anonymous_context_requests(self):
        for relative in ("scripts/coordination_check.py", "scripts/sync_docs_to_neo4j.py"):
            code = (ROOT / relative).read_text()
            self.assertIn("probe_admin_context(", code)
            self.assertNotIn("requests.get('http://localhost:8088/admin/context'", code)
            self.assertNotIn("('http://localhost:8088/admin/context',", code)

    def test_legacy_client_source_never_reads_context_body_or_uploads(self):
        code = (ROOT / "scripts/router_admin_context_readonly.py").read_text()
        for forbidden in (".json()", ".read()", "POST", "requests.post",
                          "AUTO_ROUTER_ADMIN_TOKEN = ", "http://0.0.0.0"):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
