"""Pure unittest contract: only synthetic responses, no real network."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from assistx.router_admin_client import fetch_router_ops_summary


class AdminOpsClientSafety(unittest.TestCase):
    def test_sends_token_once_never_follows_redirect(self):
        response = Mock(status_code=200)
        response.json.return_value = {"fleet_loadout_report": {"loadouts": []}}
        fetch = Mock(return_value=response)
        result = fetch_router_ops_summary(base_url="http://router.example", admin_token="synthetic", request_get=fetch)
        self.assertIn("fleet_loadout_report", result)
        fetch.assert_called_once_with(
            "http://router.example/admin/ops/summary",
            timeout=6.0,
            headers={"X-Admin-Token": "synthetic"},
            allow_redirects=False,
        )

    def test_missing_token_never_contacts_server(self):
        fetch = Mock()
        with self.assertRaises(ValueError):
            fetch_router_ops_summary(base_url="http://router.example", admin_token="", request_get=fetch)
        fetch.assert_not_called()

    def test_redirect_fails_without_follow(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                fetch = Mock(return_value=Mock(status_code=code))
                with self.assertRaisesRegex(ValueError, "redirect denied"):
                    fetch_router_ops_summary(
                        base_url="http://router.example", admin_token="synthetic", request_get=fetch
                    )
                self.assertEqual(fetch.call_count, 1)
                self.assertFalse(fetch.call_args.kwargs["allow_redirects"])

    def test_denial_not_retried(self):
        response = Mock(status_code=401)
        response.raise_for_status.side_effect = PermissionError("denied")
        fetch = Mock(return_value=response)
        with self.assertRaises(PermissionError):
            fetch_router_ops_summary(base_url="http://router.example", admin_token="synthetic", request_get=fetch)
        self.assertEqual(fetch.call_count, 1)

    def test_non_object_response_denied(self):
        response = Mock(status_code=200)
        response.json.return_value = ["invalid"]
        with self.assertRaisesRegex(ValueError, "not an object"):
            fetch_router_ops_summary(
                base_url="http://router.example", admin_token="synthetic", request_get=Mock(return_value=response)
            )

    def test_invalid_source_or_timeout_never_contacts_server(self):
        for url, timeout in (
            ("", 6),
            ("local-only", 6),
            ("http://router.example", 0),
            ("https://router.example", 20),
            ("http://fake-user:fake-password@router.example", 6),
            ("http://router.example/other-path", 6),
            ("http://router.example?bad=query", 6),
        ):
            with self.subTest(url=url, timeout=timeout):
                fetch = Mock()
                with self.assertRaises(ValueError):
                    fetch_router_ops_summary(base_url=url, admin_token="synthetic", request_get=fetch, timeout=timeout)
                fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
