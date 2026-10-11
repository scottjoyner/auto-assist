"""Synthetic LM Studio native SSE model-instance evidence; never contact a model."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_lmstudio_native_stream_instance",
    ROOT / "scripts" / "verify_lmstudio_native_stream_instance.py",
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)
INSTANCE = "liquid/lfm2.5-1.2b-instance-test"
CONTENT = "PING_OK"
RESPONSE = "resp_20261011_abcdefghijklmnop"


def event(name, **fields):
    return "event: " + name + "\ndata: " + json.dumps(
        {"type": name, **fields}, sort_keys=True
    ) + "\n\n"


def fixture():
    return [
        event("chat.start", model_instance_id=INSTANCE),
        event("message.start"),
        event("message.delta", content="PING_"),
        event("message.delta", content="OK"),
        event("message.end"),
        event("chat.end", result={
            "model_instance_id": INSTANCE, "response_id": RESPONSE,
            "output": [{"type": "message", "content": CONTENT}],
            "stats": {"total_output_tokens": 6, "input_tokens": 3},
        }),
    ]


class TestNativeSSE(unittest.TestCase):
    def check(self, stream=None, **kwargs):
        if stream is None:
            stream = fixture()
        return M.parse(
            "".join(stream).encode("utf-8"),
            expected_loaded_instance_id=INSTANCE,
            **kwargs,
        )

    def test_valid_preloaded_native_stream_evidence(self):
        result = self.check()
        self.assertEqual(result.model_instance_id, INSTANCE)
        self.assertEqual(result.response_id, RESPONSE)
        self.assertEqual(
            result.output_sha256, hashlib.sha256(b"PING_OK").hexdigest()
        )
        self.assertEqual(result.event_count, 6)
        self.assertEqual(result.output_tokens, 6)

    def test_review_does_not_mint_worker_authorization(self):
        r = M.review_only(self.check())
        self.assertEqual(r["status"], "GATEWAY_MODEL_INSTANCE_ONLY")
        for key in (
            "gateway_source_authenticated", "physical_worker_pid_attested",
            "worker_execution_attested", "owner_bound_request_attested",
            "can_sign_physical_source", "can_approve", "can_dispatch",
        ):
            self.assertIs(r[key], False)

    def test_crlf_streaming_is_supported(self):
        lines = "".join(fixture()).replace("\n", "\r\n").encode()
        self.assertEqual(
            M.parse(lines, expected_loaded_instance_id=INSTANCE).response_id,
            RESPONSE,
        )

    def test_wrong_expected_instance_refused(self):
        with self.assertRaisesRegex(ValueError, "loaded model instance"):
            M.parse(
                "".join(fixture()).encode(),
                expected_loaded_instance_id="another-preloaded-model",
            )

    def test_mismatched_chat_end_instance_refused(self):
        events = fixture()
        events[-1] = event("chat.end", result={
            "model_instance_id": "another-preloaded-model",
            "response_id": RESPONSE,
            "output": [{"type": "message", "content": CONTENT}],
            "stats": {"total_output_tokens": 6},
        })
        with self.assertRaisesRegex(ValueError, "model instance differs"):
            self.check(events)

    def test_model_load_start_refused_even_if_stream_completes(self):
        events = fixture()
        events.insert(1, event("model_load.start", model_instance_id=INSTANCE))
        with self.assertRaisesRegex(ValueError, "unadmitted event"):
            self.check(events)

    def test_model_load_progress_refused(self):
        events = fixture()
        events.insert(1, event("model_load.progress", progress=0.5))
        with self.assertRaisesRegex(ValueError, "unadmitted event"):
            self.check(events)

    def test_model_load_end_refused(self):
        events = fixture()
        events.insert(1, event("model_load.end", model_instance_id=INSTANCE))
        with self.assertRaisesRegex(ValueError, "unadmitted event"):
            self.check(events)

    def test_sse_error_event_refused_even_with_chat_end(self):
        events = fixture()
        events.insert(4, event("error", error={"type": "internal_error"}))
        with self.assertRaisesRegex(ValueError, "unadmitted event"):
            self.check(events)

    def test_reasoning_delta_refused_to_protect_sensitive_trace(self):
        events = fixture()
        events.insert(2, event("reasoning.delta", content="private thought"))
        with self.assertRaisesRegex(ValueError, "unadmitted event"):
            self.check(events)

    def test_tool_call_refused(self):
        events = fixture()
        events.insert(2, event("tool_call.start", tool="read_file"))
        with self.assertRaisesRegex(ValueError, "unadmitted event"):
            self.check(events)

    def test_missing_chat_end_refused(self):
        with self.assertRaisesRegex(ValueError, "without terminal"):
            self.check(fixture()[:-1])

    def test_missing_trailing_event_delimiter_refused(self):
        raw = "".join(fixture()).removesuffix("\n\n").encode()
        with self.assertRaisesRegex(ValueError, "incomplete SSE"):
            M.parse(raw, expected_loaded_instance_id=INSTANCE)

    def test_bogus_utf8_refused(self):
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            M.parse(b"\xff\n\n", expected_loaded_instance_id=INSTANCE)

    def test_stream_over_128kb_refused_before_json_parse(self):
        with self.assertRaisesRegex(ValueError, "size budget"):
            M.parse(
                b"x" * (M.MAX_STREAM_BYTES + 1),
                expected_loaded_instance_id=INSTANCE,
            )

    def test_stream_content_over_8kb_refused(self):
        events = fixture()
        events[2] = event("message.delta", content="Z" * 8193)
        with self.assertRaisesRegex(ValueError, "privacy budget"):
            self.check(events)

    def test_extra_event_after_chat_end_refused(self):
        with self.assertRaisesRegex(ValueError, "extra event"):
            self.check(fixture() + [event("message.end")])

    def test_duplicate_chat_start_refused(self):
        events = fixture()
        events.insert(1, events[0])
        with self.assertRaisesRegex(ValueError, "duplicated chat.start"):
            self.check(events)

    def test_message_end_without_message_start_refused(self):
        events = fixture()
        del events[1]
        with self.assertRaisesRegex(ValueError, "unexpected content delta"):
            self.check(events)

    def test_stream_final_output_mismatch_refused(self):
        events = fixture()
        events[-1] = event("chat.end", result={
            "model_instance_id": INSTANCE, "response_id": RESPONSE,
            "output": [{"type": "message", "content": "OTHER"}],
            "stats": {"total_output_tokens": 6},
        })
        with self.assertRaisesRegex(ValueError, "finalized response differ"):
            self.check(events)

    def test_unsafe_multimessage_or_tool_output_refused(self):
        events = fixture()
        events[-1] = event("chat.end", result={
            "model_instance_id": INSTANCE, "response_id": RESPONSE,
            "output": [
                {"type": "message", "content": CONTENT},
                {"type": "tool_call", "tool": "network"},
            ], "stats": {"total_output_tokens": 6},
        })
        with self.assertRaisesRegex(ValueError, "output shape"):
            self.check(events)

    def test_missing_response_id_refused(self):
        events = fixture()
        events[-1] = event("chat.end", result={
            "model_instance_id": INSTANCE,
            "output": [{"type": "message", "content": CONTENT}],
            "stats": {"total_output_tokens": 6},
        })
        with self.assertRaisesRegex(ValueError, "mandatory fields"):
            self.check(events)

    def test_fictional_response_id_refused(self):
        events = fixture()
        events[-1] = event("chat.end", result={
            "model_instance_id": INSTANCE, "response_id": "local",
            "output": [{"type": "message", "content": CONTENT}],
            "stats": {"total_output_tokens": 6},
        })
        with self.assertRaisesRegex(ValueError, "stored response_id"):
            self.check(events)

    def test_zero_token_canary_refused(self):
        events = fixture()
        events[-1] = event("chat.end", result={
            "model_instance_id": INSTANCE, "response_id": RESPONSE,
            "output": [{"type": "message", "content": CONTENT}],
            "stats": {"total_output_tokens": 0},
        })
        with self.assertRaisesRegex(ValueError, "token budget"):
            self.check(events)

    def test_over_admitted_tokens_refused(self):
        with self.assertRaisesRegex(ValueError, "token budget"):
            self.check(max_output_tokens=5)

    def test_boolean_tokens_not_accepted(self):
        events = fixture()
        events[-1] = event("chat.end", result={
            "model_instance_id": INSTANCE, "response_id": RESPONSE,
            "output": [{"type": "message", "content": CONTENT}],
            "stats": {"total_output_tokens": True},
        })
        with self.assertRaisesRegex(ValueError, "token budget"):
            self.check(events)

    def test_unapproved_content_format_refused(self):
        events = fixture()
        events[3] = event("message.delta", content={"private": "data"})
        with self.assertRaisesRegex(ValueError, "not text"):
            self.check(events)

    def test_additional_unknown_sse_metadata_refused(self):
        events = fixture()
        events[0] = event("chat.start", model_instance_id=INSTANCE, pid=4783)
        with self.assertRaisesRegex(ValueError, "duplicated chat.start"):
            self.check(events)

    def test_unauthorized_zero_or_large_canary_budget_refused(self):
        with self.assertRaisesRegex(ValueError, "output-token budget"):
            self.check(max_output_tokens=0)
        with self.assertRaisesRegex(ValueError, "output-token budget"):
            self.check(max_output_tokens=1024)


if __name__ == "__main__":
    unittest.main()
