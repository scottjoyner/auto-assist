"""Synthetic tests for secret-safe witr provenance adapter."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/experiments/witr_redacted_provenance.py"
spec = importlib.util.spec_from_file_location("witr_probe", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SECRET = "do_not_expose_this_sensitive_api_key_value"

def sample():
    return [
        {"PID": 1, "Command": "systemd --user-password=" + SECRET},
        {"PID": 42, "PPID": 1, "Command": "bash -c echo " + SECRET},
        {"PID": 4000, "PPID": 42, "Command": "llama-server --api-key " + SECRET,
         "Env": ["TOKEN=" + SECRET], "Sockets": ["secret://"+SECRET],
         "Warnings": ["private "+SECRET]},
    ]

class TestWitrProvenance(unittest.TestCase):
    def test_no_secrets_escape_even_from_unexpected_fields(self):
        out = mod.scrub_short(sample(), 4000)
        encoded = json.dumps(out)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn("Command", encoded)
        self.assertNotIn("Env", encoded)
        self.assertNotIn("Sockets", encoded)
        self.assertFalse(out["dispatch_allowed"])
        self.assertFalse(out["physical_runtime_verified"])
        self.assertFalse(out["claim_verified"])
        self.assertFalse(out["full_commands_exported"])

    def test_parent_exited_flag(self):
        rows = sample()
        rows[2]["ParentExited"] = True
        self.assertTrue(mod.scrub_short(rows, 4000)["ancestry"][-1]["reported_parent_exited"])

    def test_ppid_missing_is_unknown(self):
        self.assertIsNone(mod.scrub_short(sample(), 4000)["ancestry"][0]["reported_ppid"])

    def test_target_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            mod.scrub_short(sample(), 7)

    def test_duplicate_pid_rejected(self):
        rows = sample()
        rows[-1]["PID"] = 42
        with self.assertRaises(ValueError):
            mod.scrub_short(rows, 42)

    def test_invalid_pids_rejected(self):
        for value in (True, 0, -1, "42", 2147483648):
            with self.subTest(value=value):
                rows = sample()
                rows[2]["PID"] = value
                with self.assertRaises(ValueError):
                    mod.scrub_short(rows, 4000)

    def test_invalid_ppid_rejected(self):
        rows = sample()
        rows[-1]["PPID"] = "1"
        with self.assertRaises(ValueError):
            mod.scrub_short(rows, 4000)

    def test_invalid_parent_exited_rejected(self):
        rows = sample()
        rows[-1]["ParentExited"] = SECRET
        with self.assertRaises(ValueError):
            mod.scrub_short(rows, 4000)

    def test_empty_and_nonlist_rejected(self):
        for rows in ([], {}, "secret", None, [None]):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    mod.scrub_short(rows, 4000)

    def test_depth_limit_rejected(self):
        rows = [{"PID": i+1} for i in range(33)]
        with self.assertRaises(ValueError):
            mod.scrub_short(rows, 33)

    def test_enum_only_category(self):
        self.assertIn(mod.category(2147483647), ("unknown", "other"))

    def test_cli_fixture_no_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "data.json"
            p.write_text(json.dumps(sample()))
            run = subprocess.run([sys.executable, str(SCRIPT), "--pid", "4000",
                                  "--fixture", str(p)], capture_output=True, text=True, timeout=8)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertNotIn(SECRET, run.stdout + run.stderr)
            self.assertEqual(json.loads(run.stdout)["target_pid"], 4000)

    def test_cli_bad_json_fails_without_dumping(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "data.json"
            p.write_text('{"Command": "' + SECRET + '", "bad":NaN}')
            run = subprocess.run([sys.executable, str(SCRIPT), "--pid", "4000",
                                  "--fixture", str(p)], capture_output=True, text=True, timeout=8)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, "")
            self.assertNotIn(SECRET, run.stderr)

    def test_checksum_denied_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "witr"
            executable.write_text("#!/bin/sh\necho EXPLOITED\n")
            executable.chmod(0o755)
            with self.assertRaises(ValueError):
                mod.probe(executable, "0"*64, 4000)

if __name__ == "__main__":
    unittest.main()
