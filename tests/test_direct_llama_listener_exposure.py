"""Synthetic process/listener/model access audit; zero real network/inference."""
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
    "direct_llama_source_audit",
    ROOT / "scripts" / "audit_direct_llama_listener.py",
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)
BOOT = "d867483a-d299-49d0-ac3a-7fa4a7d41c1a"


class TestDirectListenerAudit(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.proc_root = self.root / "proc"
        self.pid = 1006
        self.port = 1235
        self.proc = self.proc_root / str(self.pid)
        self.proc.mkdir(parents=True)
        self.model = self.root / "test.gguf"
        self.model.write_bytes(b"GGUF" * 32)
        self.sha = hashlib.sha256(self.model.read_bytes()).hexdigest()
        self.boot_file = self.root / "boot_id"
        self.boot_file.write_text(BOOT + "\n")
        self.tcp = self.root / "tcp"
        self.tcp6 = self.root / "tcp6"
        self.tcp6.write_text("header\n")
        self.write_process()
        self.write_listener()

    def write_process(self, *, host="0.0.0.0", extra=(), ticks=794):
        args = [
            "/opt/binaries/llama-server", "-m", str(self.model),
            "--host", host, "--port", str(self.port), *extra,
        ]
        (self.proc / "cmdline").write_bytes(
            b"\0".join(x.encode() for x in args) + b"\0"
        )
        fields = ["S", "1"] + ["0"] * 17 + [str(ticks), "0"]
        (self.proc / "stat").write_text(
            "1006 (llama (worker)) " + " ".join(fields) + "\n"
        )
        fd_dir = self.proc / "fd"
        fd_dir.mkdir(exist_ok=True)
        link = fd_dir / "8"
        if link.is_symlink():
            link.unlink()
        link.symlink_to("socket:[765432]")

    def write_listener(self, *, address="00000000", inode="765432",
                       listening=True):
        header = (
            "sl local_address rem_address st tx_queue rx_queue tr "
            "tm->when retrnsmt uid timeout inode"
        )
        state = "0A" if listening else "01"
        line = (
            f"0: {address}:{self.port:04X} 00000000:0000 {state} "
            f"00000000:00000000 00:00000000 00000000 1000 0 "
            f"{inode} 1"
        )
        self.tcp.write_text(header + "\n" + line + "\n")

    def inspect(self, **overrides):
        kwargs = {
            "pid": self.pid,
            "port": self.port,
            "proc_root": self.proc_root,
            "boot_file": self.boot_file,
            "tcp_tables": (self.tcp, self.tcp6),
        }
        kwargs.update(overrides)
        return M.collect(**kwargs)

    def test_non_loopback_direct_listener_blocked_and_never_admitted(self):
        result = self.inspect()
        self.assertEqual(
            result["admission_status"], "BLOCKED_NON_LOOPBACK_DIRECT_LISTENER"
        )
        self.assertFalse(result["loopback_only"])
        self.assertEqual(result["listener_bindings"], ["wildcard"])
        self.assertEqual(result["pid"], 1006)
        self.assertEqual(result["process_start_ticks"], 794)
        self.assertEqual(result["model_sha256"], self.sha)
        self.assertFalse(result["explicit_api_key_flag_present"])
        self.assertIsNone(result["model_expected_digest_match"])

    def test_loopback_still_cannot_authorize_dispatch(self):
        self.write_process(host="127.0.0.1")
        self.write_listener(address="0100007F")
        r = self.inspect(expected_sha256=self.sha)
        self.assertTrue(r["loopback_only"])
        self.assertTrue(r["model_digest_independently_pinned"])
        self.assertTrue(r["model_expected_digest_match"])
        self.assertEqual(r["admission_status"], "BLOCKED_NO_TRUSTED_GATEWAY_OR_OWNER")
        for key in (
            "api_authentication_proven", "independent_worker_execution_attested",
            "authenticated_owner_receipt", "signed_source_provenance",
            "authenticated_gateway_verified", "can_approve", "can_renew",
            "can_dispatch",
        ):
            self.assertIs(r[key], False)

    def test_declared_loopback_cannot_mask_wildcard_socket(self):
        self.write_process(host="127.0.0.1")
        result = self.inspect()
        self.assertFalse(result["loopback_only"])
        self.assertEqual(
            result["admission_status"], "BLOCKED_NON_LOOPBACK_DIRECT_LISTENER"
        )

    def test_explicit_api_key_flag_is_not_authenticated_proof(self):
        self.write_process(extra=("--api-key", "synthetic-test-only-secret"))
        result = self.inspect()
        self.assertTrue(result["explicit_api_key_flag_present"])
        self.assertFalse(result["api_authentication_proven"])
        self.assertFalse(result["can_dispatch"])
        self.assertNotIn("synthetic-test-only-secret", json.dumps(result))

    def test_wrong_expected_digest_never_matches(self):
        result = self.inspect(expected_sha256="f" * 64)
        self.assertIs(result["model_expected_digest_match"], False)
        self.assertFalse(result["can_approve"])

    def test_wrong_model_size_budget_rejected_before_hashing(self):
        with self.assertRaisesRegex(ValueError, "budget"):
            self.inspect(max_model_bytes=12)

    def test_missing_pid_socket_owner_rejected(self):
        self.write_listener(inode="666666")
        with self.assertRaisesRegex(ValueError, "not owned"):
            self.inspect()

    def test_established_connection_is_not_listener(self):
        self.write_listener(listening=False)
        with self.assertRaisesRegex(ValueError, "not owned"):
            self.inspect()

    def test_wrong_declared_port_refused(self):
        with self.assertRaisesRegex(ValueError, "port differs"):
            self.inspect(port=1222)

    def test_missing_or_ambiguous_model_flag_refused(self):
        self.write_process(extra=("--model", str(self.model)))
        with self.assertRaisesRegex(ValueError, "ambiguous model"):
            self.inspect()

    def test_symlinked_model_file_refused(self):
        original = self.model
        link = self.root / "model_link.gguf"
        link.symlink_to(original)
        self.model = link
        self.write_process()
        self.model = original
        with self.assertRaisesRegex(ValueError, "nonsymlink"):
            self.inspect()

    def test_wrong_process_basename_refused(self):
        raw = (self.proc / "cmdline").read_bytes().replace(
            b"llama-server", b"lm-studio",
        )
        (self.proc / "cmdline").write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "not an independently"):
            self.inspect()

    def test_process_restart_during_hashing_refused(self):
        real = M.hashlib.file_digest
        def race(model_file, algorithm):
            digest = real(model_file, algorithm)
            self.write_process(ticks=800)
            return digest
        with patch.object(M.hashlib, "file_digest", side_effect=race):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.inspect()

    def test_model_modified_during_hashing_refused(self):
        real = M.hashlib.file_digest
        def race(model_file, algorithm):
            digest = real(model_file, algorithm)
            self.model.write_bytes(b"changed-after-hash")
            return digest
        with patch.object(M.hashlib, "file_digest", side_effect=race):
            with self.assertRaisesRegex(ValueError, "model changed"):
                self.inspect()

    def test_output_does_not_disclose_model_path_or_cmdline(self):
        report = self.inspect()
        payload = json.dumps(report)
        self.assertNotIn(str(self.root), payload)
        self.assertNotIn("--host", payload)
        self.assertNotIn("--model", payload)

    def test_non_loopback_specific_address_blocked(self):
        self.write_process(host="192.168.1.20")
        self.write_listener(address="1401A8C0")
        result = self.inspect()
        self.assertEqual(result["listener_bindings"], ["non_loopback"])
        self.assertFalse(result["loopback_only"])

    def test_zero_boolean_or_invalid_sha256_refused(self):
        for bad in (True, 0, -1, 2**40):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.inspect(pid=bad)
        for bad in ("UPPERCASE", "a" * 63, "z" * 64, False):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.inspect(expected_sha256=bad)


if __name__ == "__main__":
    unittest.main()
