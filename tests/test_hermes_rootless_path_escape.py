"""Negative path-escape cases for the research-only rootless Hermes entrypoint.

No Docker, provider, network, real credentials, or production directories.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/hermes-rootless/entrypoint.sh"

@unittest.skipIf(os.geteuid() == 0, "filesystem isolation runs under nonroot user")
class RootlessPathEscapeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rootless-path-escape-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.hermes = self.home / "hermes-home"
        self.hermes.mkdir()
        (self.hermes / "config.yaml").write_text("synthetic: only\n")
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def run_case(self, *, home=None, hermes=None, config=None):
        env = {
            "HOME": str(home if home is not None else self.home),
            "HERMES_HOME": str(hermes if hermes is not None else self.hermes),
            "HERMES_CONFIG_HOME": str(config if config is not None else self.home / ".hermes"),
            "PATH": "/usr/bin:/bin",
            "FLEET_ROUTER_BEARER_TOKEN": "SYNTHETIC_TOKEN_NO_SECRET",
        }
        return subprocess.run(["/bin/bash", str(SCRIPT), "/bin/true"],
                              text=True, capture_output=True,
                              timeout=8, check=False, env=env)

    def assert_denied(self, **kwargs):
        result = self.run_case(**kwargs)
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertNotIn("SYNTHETIC_TOKEN_NO_SECRET", result.stderr)

    def test_home_traversal_is_denied(self):
        self.assert_denied(home=self.root / "outside" / ".." / "home")

    def test_symlinked_home_is_denied(self):
        link = self.root / "home-alias"
        link.symlink_to(self.home, target_is_directory=True)
        self.assert_denied(home=link, hermes=link / "hermes-home", config=link / ".hermes")

    def test_absolute_hermes_home_outside_home_denied(self):
        self.assert_denied(hermes=self.outside / "store")

    def test_hermes_home_traversal_denied(self):
        self.assert_denied(hermes=self.home / ".." / "outside" / "store")

    def test_hermes_home_alternative_inside_home_denied(self):
        self.assert_denied(hermes=self.home / "arbitrary")

    def test_config_home_outside_home_denied(self):
        self.assert_denied(config=self.outside / ".hermes")

    def test_config_home_traversal_denied(self):
        self.assert_denied(config=self.home / ".." / "outside" / ".hermes")

    def test_hermes_home_symlink_denied(self):
        (self.hermes / "config.yaml").unlink()
        self.hermes.rmdir()
        self.hermes.symlink_to(self.outside, target_is_directory=True)
        self.assert_denied()
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_config_dir_symlink_denied(self):
        (self.home / ".hermes").symlink_to(self.outside, target_is_directory=True)
        self.assert_denied()
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_sessions_directory_symlink_denied(self):
        (self.hermes / "sessions").symlink_to(self.outside, target_is_directory=True)
        self.assert_denied()
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_primary_config_symlink_denied(self):
        (self.hermes / "config.yaml").unlink()
        target = self.outside / "protected.txt"
        target.write_text("unchanged\n")
        (self.hermes / "config.yaml").symlink_to(target)
        self.assert_denied()
        self.assertEqual(target.read_text(), "unchanged\n")

    def test_unexpected_config_symlink_denied(self):
        target = self.outside / "config.yaml"
        target.write_text("unchanged\n")
        config_dir = self.home / ".hermes"
        config_dir.mkdir()
        (config_dir / "config.yaml").symlink_to(target)
        self.assert_denied()
        self.assertEqual(target.read_text(), "unchanged\n")

    def test_unexpected_config_file_denied(self):
        config_dir = self.home / ".hermes"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("unexpected\n")
        self.assert_denied()
        self.assertEqual((config_dir / "config.yaml").read_text(), "unexpected\n")

    def test_legitimate_existing_symlink_and_idempotence(self):
        self.assertEqual(self.run_case().returncode, 0)
        self.assertEqual(self.run_case().returncode, 0)
        link = self.home / ".hermes/config.yaml"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(self.hermes / "config.yaml"))
        self.assertFalse((self.home / ".hermes/.env").exists())
        self.assertFalse((self.hermes / ".env").exists())

    def test_hermes_subdirectory_as_regular_file_denied(self):
        (self.hermes / "sessions").write_text("not a directory\n")
        self.assert_denied()

if __name__ == "__main__":
    unittest.main()
