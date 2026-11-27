"""Offline validation of the explicit opt-in hotpath research experiment.

These tests do not download Rust dependencies, compile native code, profile
services or run the Rust test workload.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts/experiments/hotpath_shadow_overhead.py"
spec = importlib.util.spec_from_file_location("hotpath_shadow_overhead", MODULE)
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)

REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"


def lockfile() -> str:
    return (
        'version = 3\n'
        '[[package]]\nname = "fleet_hotpath_synthetic_pilot"\nversion = "0.1.0"\n'
        '[[package]]\nname = "hotpath"\nversion = "0.30.3"\n'
        f'source = "{REGISTRY}"\nchecksum = "{"a"*64}"\n'
    )


class TestHotpathProbe(unittest.TestCase):
    def test_default_does_not_fetch_or_execute(self):
        with self.assertRaises(hp.Inconclusive):
            hp.run(Path("/definitely/does-not-exist"), 6, False)

    def test_cli_default_fails_closed(self):
        result = subprocess.run(
            [sys.executable, str(MODULE), "--cargo", "/definitely/not-cargo"],
            capture_output=True, text=True, timeout=8,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_direct_package_version_pin(self):
        self.assertIn('version = "=0.30.3"', hp.MANIFEST)
        self.assertIn('default-features = false', hp.MANIFEST)
        self.assertIn('profile = ["dep:hotpath", "hotpath/hotpath"]', hp.MANIFEST)

    def test_upstream_cloud_mcp_not_selected(self):
        for name in ("hotpath-cloud", "hotpath-mcp", "hotpath-prometheus", "hotpath-cpu"):
            self.assertNotIn(name, hp.MANIFEST)

    def test_ordinary_build_has_no_hotpath_macros(self):
        self.assertIn('#[cfg_attr(feature = "profile", hotpath::measure)]', hp.SOURCE)
        self.assertIn('#[cfg_attr(feature = "profile", hotpath::main(', hp.SOURCE)

    def test_pinned_lock_checks_success(self):
        h = hp.verify_lock(lockfile())
        self.assertRegex(h, r"^[a-f0-9]{64}$")

    def test_wrong_version_refused(self):
        with self.assertRaises(hp.Inconclusive):
            hp.verify_lock(lockfile().replace('"0.30.3"', '"0.30.2"'))

    def test_git_dependency_refused(self):
        with self.assertRaises(hp.Inconclusive):
            hp.verify_lock(lockfile().replace(REGISTRY, "git+https://example.com/mirror"))

    def test_checksum_missing_refused(self):
        with self.assertRaises(hp.Inconclusive):
            hp.verify_lock(lockfile().replace(f'checksum = "{"a"*64}"', ""))

    def test_fake_local_dependency_refused(self):
        text = lockfile() + '\n[[package]]\nname = "untrusted_local"\nversion = "1.0.0"\n'
        with self.assertRaises(hp.Inconclusive):
            hp.verify_lock(text)

    def test_malformed_lockfile_refused(self):
        with self.assertRaises(Exception):
            hp.verify_lock('not a valid lockfile!')

    def test_valid_profiled_observation(self):
        sample = b"checksum=103f8b718d0e63dc iterations=14000\n[hotpath] timing synthetic_work\n"
        checksum, report = hp.parse_observation(sample, b"", True)
        self.assertEqual(checksum, "checksum=103f8b718d0e63dc iterations=14000")
        self.assertTrue(report)

    def test_baseline_observation(self):
        checksum, report = hp.parse_observation(
            b"checksum=103f8b718d0e63dc iterations=14000\n", b"", False
        )
        self.assertTrue(checksum.startswith("checksum="))
        self.assertFalse(report)

    def test_profile_without_report_refused(self):
        with self.assertRaises(hp.Inconclusive):
            hp.parse_observation(b"checksum=103f8b718d0e63dc iterations=14000\n", b"", True)

    def test_baseline_with_profiler_refused(self):
        with self.assertRaises(hp.Inconclusive):
            hp.parse_observation(
                b"checksum=103f8b718d0e63dc iterations=14000\n[hotpath] timing synthetic_work",
                b"", False
            )

    def test_invalid_checksum_refused(self):
        with self.assertRaises(hp.Inconclusive):
            hp.parse_observation(b"checksum=MALFORMED\n", b"", False)

    def test_large_report_refused(self):
        with self.assertRaises(hp.Inconclusive):
            hp.parse_observation(b"x"*100001, b"", True)

    def test_sample_bounds(self):
        for samples in (0, 1, 2, 11, 100):
            with self.subTest(samples=samples), self.assertRaises(hp.Inconclusive):
                hp.stats([1.0] * samples)

    def test_sample_stats(self):
        report = hp.stats([4., 2., 5., 3., 1.])
        self.assertEqual(report, {"samples": 5, "median_ms": 3.0, "min_ms": 1.0, "max_ms": 5.0})

    def test_invalid_timings_refused(self):
        for value in (0., -1., float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(hp.Inconclusive):
                hp.stats([1., 2., value])

    def test_downloads_authorization_checked_before_cargo_path(self):
        # Offline/default refusal should happen before ever invoking rustup.
        with tempfile.TemporaryDirectory() as work:
            fake = Path(work) / "cargo"
            fake.write_text("not an executable")
            with self.assertRaises(hp.Inconclusive):
                hp.run(fake, 6, False)


if __name__ == "__main__":
    unittest.main()
