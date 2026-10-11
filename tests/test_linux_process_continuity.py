"""Offline fake-/proc acceptance for an UNSIGNED GGUF continuity observation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "host_continuity_witness", ROOT / "scripts" / "collect_linux_process_continuity.py"
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)

BOOT = "d867483a-d299-49d0-ac3a-7fa4a7d41c1a"


class TestSourceProcessContinuity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.model = self.root / "fixture.gguf"
        self.model.write_bytes(b"GGUF" * 37)
        self.sha256 = hashlib.sha256(self.model.read_bytes()).hexdigest()
        self.proc_root = self.root / "proc"
        self.proc_root.mkdir()
        self.boot = self.root / "boot_id"
        self.boot.write_text(BOOT + "\n")
        self.pid = 1234
        self.proc = self.proc_root / str(self.pid)
        self.proc.mkdir()
        self.write_process()

    def write_process(self, ticks: int = 2340, executable="llama-server",
                      extra_args=()):
        args = [
            "/usr/local/bin/" + executable, "-m", str(self.model),
            "--ctx-size", "8192", *extra_args,
        ]
        (self.proc / "cmdline").write_bytes(
            b"\0".join(a.encode() for a in args) + b"\0"
        )
        rest = ["S"] + ["0"] * 18 + [str(ticks), "0"]
        (self.proc / "stat").write_text(
            "1234 (a misleading ) process name) " + " ".join(rest) + "\n"
        )

    def read(self, **overrides):
        args = dict(
            pid=self.pid, expected_digest=self.sha256,
            expected_bytes=self.model.stat().st_size,
            proc_root=self.proc_root, boot_id_file=self.boot,
        )
        args.update(overrides)
        return M.collect(**args)

    def test_exact_process_and_model_hash_observed_without_signing(self):
        result = self.read()
        self.assertEqual(result["pid"], 1234)
        self.assertEqual(result["process_start_ticks"], 2340)
        self.assertEqual(result["boot_id"], BOOT)
        self.assertEqual(
            result["model_artifact_fingerprint"], "sha256:" + self.sha256
        )
        self.assertEqual(result["source_kind"], "unsigned_host_local_proc")
        self.assertFalse(result["signature_verified"])
        self.assertFalse(result["model_loaded_attested"])
        self.assertFalse(result["can_approve"])
        self.assertFalse(result["can_dispatch"])

    def test_wrong_sha256_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.read(expected_digest="0" * 64)

    def test_wrong_file_size_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "size"):
            self.read(expected_bytes=100)

    def test_wrong_executable_refused(self):
        self.write_process(executable="python3")
        with self.assertRaisesRegex(ValueError, "llama-server"):
            self.read()

    def test_pid_start_ticks_not_inferred_from_pid(self):
        self.write_process(ticks=0)
        with self.assertRaisesRegex(ValueError, "start ticks"):
            self.read()

    def test_invalid_machine_boot_uuid_refused(self):
        self.boot.write_text("fake-boot-id")
        with self.assertRaises(ValueError):
            self.read()

    def test_unknown_or_ambiguous_model_flags_refused(self):
        self.write_process(extra_args=("--model", str(self.model)))
        with self.assertRaisesRegex(ValueError, "one explicit model"):
            self.read()

    def test_symlink_model_path_refused(self):
        link = self.root / "model-link.gguf"
        link.symlink_to(self.model)
        old = self.model
        self.model = link
        self.write_process()
        self.model = old
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            self.read()

    def test_pid_reuse_during_observation_refused(self):
        real = M.read_process_identity
        attempts = [0]

        def altered(*args):
            ticks, raw, model = real(*args)
            attempts[0] += 1
            return (ticks + 1 if attempts[0] == 2 else ticks, raw, model)

        with patch.object(M, "read_process_identity", side_effect=altered):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                self.read()

    def test_unsafe_large_model_is_refused_before_hashing(self):
        with self.assertRaisesRegex(ValueError, "bounded GGUF"):
            self.read(expected_bytes=M.MAX_MODEL_BYTES + 1)

    def test_collector_outputs_no_file_path_or_model_content(self):
        payload = json.dumps(self.read())
        self.assertNotIn(str(self.root), payload)
        self.assertNotIn("GGUF", payload)

    def test_process_stat_parser_handles_parentheses_and_spaces(self):
        rest = ["S"] + ["0"] * 18 + ["2389", "0"]
        stat = "1234 (strange comm ( extra ) name) " + " ".join(rest)
        self.assertEqual(M.process_start_ticks(stat), 2389)


if __name__ == "__main__":
    unittest.main()
