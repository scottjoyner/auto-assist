import importlib.util
import json
import pathlib
import threading
import unittest
import urllib.request
from typing import Any
from unittest import mock


BRIDGE = pathlib.Path(__file__).parents[1] / "bridges" / "signal-to-assistx-bridge.py"
spec = importlib.util.spec_from_file_location("signal_to_assistx_bridge", BRIDGE)
assert spec is not None and spec.loader is not None
bridge: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class SignalEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.old_signal_from = bridge.SIGNAL_FROM
        bridge.SIGNAL_FROM = "+15550000001"

    def tearDown(self):
        bridge.SIGNAL_FROM = self.old_signal_from

    def test_direct_note_to_self_is_accepted(self):
        env = {
            "sourceNumber": "+15550000001",
            "dataMessage": {"message": "direct note", "timestamp": 101},
        }
        parsed = bridge.parse_envelope(env)
        self.assertEqual(parsed, ("direct note", 101, "+15550000001"))

    def test_valid_direct_message_under_existing_policy_is_accepted(self):
        bridge.SIGNAL_FROM = ""
        env = {
            "sourceNumber": "+15550000002",
            "dataMessage": {"message": "direct message", "timestamp": 102},
        }
        self.assertEqual(bridge.parse_envelope(env)[0], "direct message")

    def test_incoming_group_message_is_ignored(self):
        env = {
            "sourceNumber": "+15550000002",
            "dataMessage": {
                "message": "group request",
                "groupInfo": {"groupId": "abc"},
            },
        }
        self.assertTrue(bridge.has_group_context(env))
        self.assertIsNone(bridge.parse_envelope(env))

    def test_group_sync_echo_is_ignored_before_sync_classification(self):
        env = {
            "syncMessage": {
                "sentMessage": {
                    "dataMessage": {
                        "message": "own group echo",
                        "groupInfo": {"groupId": "abc"},
                    }
                }
            }
        }
        self.assertIsNone(bridge.parse_envelope(env))

    def test_ambiguous_group_metadata_fails_closed(self):
        for key in ("groupInfo", "groupId", "groupV2", "groupContext"):
            with self.subTest(key=key):
                env = {
                    "sourceNumber": "+15550000001",
                    "dataMessage": {"message": "ambiguous", key: None},
                }
                self.assertTrue(bridge.has_group_context(env))
                self.assertIsNone(bridge.parse_envelope(env))

    def test_http_group_message_creates_no_task_and_returns_group_reason(self):
        server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {
                "sourceNumber": "+15550000002",
                "dataMessage": {
                    "message": "do not action",
                    "groupInfo": {"groupId": "abc"},
                },
            }
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/webhook/signal",
                data=json.dumps(env).encode(),
                headers={"Content-Type": "application/json"},
            )
            with mock.patch.object(bridge, "create_assistx_task", side_effect=AssertionError("group task")):
                with urllib.request.urlopen(request, timeout=2) as response:
                    body = json.loads(response.read())
            self.assertEqual(body, {"ok": True, "action": "ignored", "reason": "group_message"})
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_direct_sync_message_without_group_is_accepted(self):
        env = {
            "syncMessage": {
                "sentMessage": {
                    "dataMessage": {
                        "message": "direct sync note",
                        "timestamp": 103,
                    }
                }
            }
        }
        self.assertEqual(bridge.parse_envelope(env)[0], "direct sync note")


if __name__ == "__main__":
    unittest.main()
