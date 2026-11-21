"""Research-only rootless Hermes entrypoint contracts; never starts Docker."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "hermes-rootless" / "entrypoint.sh"
DOCKERFILE = SCRIPT.parent / "Dockerfile"
DOCS = SCRIPT.parent / "README.md"

class RootlessHermesCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-nonroot-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.hermes = self.home / "hermes-home"
        self.hermes.mkdir(parents=True)
        (self.hermes / "config.yaml").write_text("provider: synthetic\n")

    def run_script(self, *, extra=None, home=None, hermes=None, argv=None):
        env = {
            "HOME": str(home if home is not None else self.home),
            "HERMES_HOME": str(hermes if hermes is not None else self.hermes),
            "HERMES_CONFIG_HOME": str(self.home / ".hermes"),
            "PATH": "/usr/bin:/bin",
            "FLEET_ROUTER_BEARER_TOKEN": "SYNTHETIC_NOT_A_CREDENTIAL",
        }
        env.update(extra or {})
        return subprocess.run(
            ["/bin/bash", str(SCRIPT), *(argv or ["/bin/true"])],
            capture_output=True, text=True, timeout=8, check=False, env=env,
        )

    def test_shell_syntax(self):
        self.assertEqual(subprocess.run(
            ["/bin/bash", "-n", str(SCRIPT)],
            capture_output=True, text=True, check=False,
        ).returncode, 0)

    @unittest.skipIf(os.geteuid() == 0, "nonroot contract needs nonroot host")
    def test_nonroot_runs_without_serializing_environment(self):
        result = self.run_script(argv=["/bin/sh", "-c", 'test -L "$HERMES_CONFIG_HOME/config.yaml"'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".hermes/config.yaml").is_symlink())
        self.assertFalse((self.home / ".hermes/.env").exists())
        self.assertFalse((self.hermes / ".env").exists())
        for file in self.root.rglob("*"):
            if file.is_file() and not file.is_symlink():
                self.assertNotIn(b"SYNTHETIC_NOT_A_CREDENTIAL", file.read_bytes())

    @unittest.skipIf(os.geteuid() == 0, "nonroot contract needs nonroot host")
    def test_declines_seeded_environment(self):
        result = self.run_script(extra={"ENV_SEED": str(self.root / "forbidden")})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "forbidden").exists())

    @unittest.skipIf(os.geteuid() == 0, "nonroot contract needs nonroot host")
    def test_denies_root_owned_target(self):
        result = self.run_script(hermes=Path("/root/hermes"))
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipIf(os.geteuid() == 0, "nonroot contract needs nonroot host")
    def test_denies_relative_home(self):
        result = self.run_script(home=Path("relative"))
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipIf(os.geteuid() == 0, "nonroot contract needs nonroot host")
    def test_propagates_process_exit(self):
        result = self.run_script(argv=["/bin/sh", "-c", "exit 23"])
        self.assertEqual(result.returncode, 23)

    def test_candidate_script_never_uses_legacy_env_serialization(self):
        script = SCRIPT.read_text()
        self.assertIn("root execution denied", script)
        self.assertIn("credential environment seeding", script)
        self.assertNotIn("env | grep", script)
        self.assertNotIn("/root/.hermes/.env", script)

    def test_overlay_requires_explicit_base_image(self):
        build = DOCKERFILE.read_text()
        self.assertIn("ARG HERMES_BASE_IMAGE", build)
        self.assertIn("FROM $" + "{HERMES_BASE_IMAGE}", build)
        self.assertIn('assistx.release.authorized="false"', build)
        self.assertNotIn("curl ", build)
        self.assertNotIn("apt-get", build)

    def test_docs_require_isolation_and_review(self):
        docs = DOCS.read_text()
        for required in ("HOLD", "network=none", "no-new-privileges",
                         "source-image provenance", "no production mounts",
                         "SOURCE_DATE_EPOCH"):
            self.assertIn(required, docs)

if __name__ == "__main__":
    unittest.main()
