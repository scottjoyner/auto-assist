"""Synthetic installation-path and fail-closed tests; no systemd or actual guards invoked."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "scripts" / "recovery_memory_guard_installation_preflight.py"
spec = importlib.util.spec_from_file_location("memory_guard_install_preflight", SRC)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class EntryPointPreflight(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="guard-install-preflight-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "packages" / "auto-assist"
        self.scripts = self.root / "scripts"
        self.scripts.mkdir(parents=True)
        self.entry = self.scripts / "recovery-memory-guard.py"
        # Payload would explode if imported/executed; preflight only reads bytes.
        self.entry.write_text("raise RuntimeError('never execute live guard')\n")
        self.entry.chmod(0o644)
        self.digest = hashlib.sha256(self.entry.read_bytes()).hexdigest()
        self.unit = Path(temp.name) / "guard.service"
        self.unit.write_text(self.unit_text())
        self.unit.chmod(0o644)

    def unit_text(self, *, user="assistx-recovery", script=None, root=None):
        root = str(self.root) if root is None else root
        script = str(self.entry) if script is None else script
        return ("[Unit]\nDescription=Fixture\n[Service]\n"
                "User=" + user + "\nWorkingDirectory=" + root
                + "\nExecStart=/usr/bin/python3 " + script + "\n")

    def check(self):
        return guard.inspect(self.unit, self.root, self.digest)

    def denied(self, expected_reason):
        got = self.check()
        self.assertEqual(got["reason"], expected_reason)
        self.assertEqual(got["decision"], "HOLD")
        self.assertFalse(got["entrypoint_identity_matched"])
        self.assertFalse(got["runtime_guard_evaluated"])
        self.assertFalse(got["deployment_authorized"])
        self.assertFalse(got["recovery_service_started"])
        self.assertEqual(got["model_calls"], 0)
        self.assertEqual(got["nas_payload_reads"], 0)
        self.assertNotIn("never execute", json.dumps(got))
        return got

    def test_exact_identity_still_never_authorizes_service(self):
        got = self.check()
        self.assertEqual(got["reason"], "file_identity_only_runtime_acceptance_not_verified")
        self.assertTrue(got["entrypoint_identity_matched"])
        self.assertEqual(got["decision"], "HOLD")
        self.assertFalse(got["runtime_guard_evaluated"])
        self.assertFalse(got["deployment_authorized"])

    def test_cli_always_exit_2_even_when_file_matches(self):
        with patch.object(sys, "argv", ["preflight", "--unit-file", str(self.unit),
                 "--package-root", str(self.root), "--expected-entrypoint-sha256",
                 self.digest]), patch("builtins.print") as report:
            self.assertEqual(guard.main(), 2)
            self.assertEqual(json.loads(report.call_args.args[0])["decision"], "HOLD")

    def test_missing_entrypoint_refused(self):
        self.entry.unlink()
        self.denied("path_absent_or_inaccessible")

    def test_changed_entrypoint_refused(self):
        self.entry.write_text("print('changed synthetic file')\n")
        self.denied("entrypoint_digest_mismatch")

    def test_symlink_entrypoint_refused(self):
        original = self.entry.with_name("other-synthetic.py")
        self.entry.rename(original)
        self.entry.symlink_to(original)
        self.denied("symlink_component")

    def test_symlink_parent_refused(self):
        other = self.scripts.with_name("scripts-other")
        self.scripts.rename(other)
        self.scripts.symlink_to(other, target_is_directory=True)
        self.denied("symlink_component")

    def test_fifo_has_no_writer_and_is_rejected_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("No FIFO on this platform")
        self.entry.unlink()
        os.mkfifo(self.entry, 0o600)
        self.denied("file_not_regular_or_bounded")

    def test_group_writable_entrypoint_refused(self):
        self.entry.chmod(0o664)
        self.denied("file_writable_by_group_or_other")

    def test_wrong_service_user_refused(self):
        self.unit.write_text(self.unit_text(user="root"))
        self.denied("unexpected_service_user")

    def test_changed_working_directory_refused(self):
        self.unit.write_text(self.unit_text(root="/tmp"))
        self.denied("unexpected_working_directory")

    def test_exec_points_to_different_file_refused(self):
        self.unit.write_text(self.unit_text(script="/tmp/other-guard.py"))
        self.denied("unexpected_exec_start")

    def test_duplicate_service_exec_refused(self):
        with self.unit.open("a") as file:
            file.write("ExecStart=/bin/echo wrong\n")
        self.denied("ambiguous_service_unit")

    def test_missing_service_section_refused(self):
        self.unit.write_text("[Unit]\nExecStart=/usr/bin/python3 bad.py\n")
        self.denied("incomplete_service_unit")

    def test_symlink_unit_refused(self):
        original = self.unit.with_suffix(".real")
        self.unit.rename(original)
        self.unit.symlink_to(original)
        self.denied("symlink_component")

    def test_oversized_entrypoint_refused(self):
        self.entry.write_bytes(b"a" * (guard.MAX_SCRIPT_BYTES + 1))
        self.denied("file_not_regular_or_bounded")

    def test_invalid_digest_refused(self):
        got = guard.inspect(self.unit, self.root, "abc")
        self.assertEqual(got["reason"], "invalid_expected_digest")
        self.assertFalse(got["deployment_authorized"])


if __name__ == "__main__":
    unittest.main()
