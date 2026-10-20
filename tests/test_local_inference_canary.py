"""Hermetic tests for the metadata-only local llama.cpp acceptance canary."""
import importlib.util
import io
import json
import os
import pathlib
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "scripts/local_inference_canary.py"
SPEC = importlib.util.spec_from_file_location("local_inference_canary", SOURCE)
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


def args(**kw):
    data = {"endpoint": "http://127.0.0.1:1236", "node": "test-node",
            "n_predict": 12, "timeout": 3, "allow_private": False,
            "model": "test-resident-model", "stream": False}
    data.update(kw)
    return SimpleNamespace(**data)


class CanaryContracts(unittest.TestCase):
    def test_private_route_requires_explicit_intent(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            canary.endpoint_url("http://192.168.1.128:1236", False)
        self.assertEqual(canary.endpoint_url("http://192.168.1.128:1236", True),
                         "http://192.168.1.128:1236")

    def test_no_public_endpoint_or_untrusted_port(self):
        for url in ("http://8.8.8.8:1236", "http://127.0.0.1:8088",
                    "https://127.0.0.1:1236", "http://localhost:1236",
                    "http://127.0.0.1:1236/foo", "http://me:pw@127.0.0.1:1236",
                    "http://127.0.0.1:1236/?query=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                canary.endpoint_url(url, True)

    def test_nonnegative_int_or_unknown_not_fake_zero(self):
        self.assertEqual(canary.safe_int({"t": 12}, "t"), 12)
        for v in (None, "12", -4, True, 1.5):
            self.assertIsNone(canary.safe_int({"t": v}, "t"))
        self.assertIsNone(canary.safe_int({}, "t"))

    def test_bounded_generation_and_metadata_only(self):
        answers = [
            (200, {"status": "ok"}),
            (200, {"default_generation_settings": {"model": "private-path"}}),
            (200, {"choices": [{"message": {"content": " READY "}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 5, "completion_tokens": 10,
                   "total_tokens": 15, "prompt_tokens_details": {"cached_tokens": 1}},
                   "timings": {"prompt_n": 4, "cache_n": 1,
                   "predicted_n": 10, "predicted_per_second": 42.0}}),
        ]
        with patch.object(canary, "request_json", side_effect=answers) as transport:
            result = canary.run(args())
        self.assertEqual(result["result"], "completed")
        self.assertTrue(result["matched_expected_ready"])
        self.assertEqual(result["backend_counts"]["completion_tokens"], 10)
        self.assertEqual(result["backend_counts"]["prompt_tokens"], 5)
        self.assertIsNone(result["billing_cost_usd"])
        self.assertFalse(result["gateway_proven"])
        serialized = json.dumps(result)
        self.assertNotIn("private-path", serialized)
        self.assertNotIn(" READY ", serialized)
        self.assertNotIn("Reply with exactly", serialized)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(transport.call_args[0][1], "/v1/chat/completions")
        self.assertLessEqual(transport.call_args[0][2]["max_tokens"], 32)

    def test_nonready_never_generates(self):
        with patch.object(canary, "request_json", return_value=(200, {"status": "loading"})) as transport:
            result = canary.run(args())
        self.assertEqual(result["result"], "unavailable")
        self.assertEqual(transport.call_count, 1)

    def test_missing_usage_stays_unknown(self):
        sequence = [(200, {"status": "ok"}), (200, {}),
                    (200, {"choices": [{"message": {"content": "oops"}}]})]
        with patch.object(canary, "request_json", side_effect=sequence):
            result = canary.run(args())
        self.assertIsNone(result["backend_counts"]["completion_tokens"])
        self.assertFalse(result["matched_expected_ready"])

    def test_excessive_output_limit_rejected_before_request(self):
        with patch.object(canary, "request_json") as transport:
            with self.assertRaisesRegex(ValueError, "n_predict"):
                canary.run(args(n_predict=1000))
            transport.assert_not_called()

    def test_protected_exclusive_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "receipts" / "test.json"
            canary.write_private(str(target), {"result": "completed", "raw": None})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(json.loads(target.read_text())["result"], "completed")
            with self.assertRaises(FileExistsError):
                canary.write_private(str(target), {"result": "overwritten"})

    def test_stream_done_and_observed_usage(self):
        rows = [
            b'data: {"choices":[{"delta":{"content":"READY"},"finish_reason":"stop"}]}' + bytes([10]),
            b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":3,"total_tokens":15}}' + bytes([10]),
            b'data: [DONE]' + bytes([10]),
        ]
        stream = io.BytesIO(b"".join(rows))
        with patch.object(canary._OPENER, "open", return_value=stream):
            output = canary.stream_chat("http://127.0.0.1:1236",
                                        {"model": "test", "messages": []}, 2)
        self.assertTrue(output["stream_done"])
        self.assertEqual(output["choices"][0]["message"]["content"], "READY")
        self.assertEqual(output["usage"]["total_tokens"], 15)
        self.assertIsNotNone(output["first_content_ms"])

    def test_stream_without_done_fails_closed(self):
        stream = io.BytesIO(b'data: {"choices":[{"delta":{"content":"READY"}}]}' + bytes([10]))
        with patch.object(canary._OPENER, "open", return_value=stream):
            with self.assertRaisesRegex(ValueError, "without DONE"):
                canary.stream_chat("http://127.0.0.1:1236",
                                    {"model": "test", "messages": []}, 2)


if __name__ == "__main__":
    unittest.main()
