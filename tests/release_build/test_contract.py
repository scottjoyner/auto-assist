import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_build", ROOT / "scripts/verify-release-build.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BuildContractTests(unittest.TestCase):
    def validate(self, dockerfile):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Dockerfile").write_text(dockerfile)
            (root / "scripts").mkdir()
            for name in ("deploy-e2e-canary.sh", "rollback-e2e-canary.sh"):
                (root / "scripts" / name).write_text("set -euo pipefail\n")
            return module.check(root)

    def test_current_tree(self):
        self.assertEqual(module.check(ROOT), [])

    def test_masked_install_rejected(self):
        self.assertTrue(self.validate("RUN pip install example | tail -5\nRUN pip check\n"))

    def test_masked_download_rejected(self):
        self.assertTrue(self.validate("RUN curl example | bash -\nRUN pip check\n"))

    def test_success_override_rejected(self):
        self.assertTrue(self.validate("RUN pip install example || true\nRUN pip check\n"))

    def test_missing_source_rejected(self):
        self.assertTrue(self.validate("COPY absent /app\nRUN pip check\n"))

    def test_dependency_check_order(self):
        self.assertTrue(self.validate("RUN pip check\nRUN pip install example\n"))

    def test_fail_fast_install_allowed(self):
        self.assertEqual(self.validate("RUN pip install example && pip --version\nRUN pip check\n"), [])
