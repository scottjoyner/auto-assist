"""No-file-content negative controls for protected evidence mount exclusions."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/hermes-rootless/verify_evidence_exclusion.py"
SPEC = importlib.util.spec_from_file_location("verify_evidence_exclusion", SCRIPT)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class EvidenceExclusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="og-hermes-evidence-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.protected = self.root / "ssd" / "knowledge" / "watchdog-witness"
        self.protected.mkdir(parents=True)
        self.approved = self.root / "scratch" / "knowledge"
        self.workspace = self.root / "scratch" / "workspace"
        self.approved.mkdir(parents=True)
        self.workspace.mkdir()
        self.record = {"Mounts": [
            self.mount(self.approved, "/research/knowledge", False),
            self.mount(self.workspace, "/workspace", True),
        ]}

    @staticmethod
    def mount(path: Path, dest: str, writable: bool) -> dict:
        return {"Type": "bind", "Source": str(path),
                "Destination": dest, "RW": writable}

    def check(self, data=None):
        return guard.audit(self.record if data is None else data,
                           protected_roots=(str(self.protected),))

    def test_exact_two_mount_fixture_passes_without_release(self):
        got = self.check()
        self.assertTrue(got["protected_evidence_path_exclusion_pass"], got)
        self.assertEqual(got["protected_overlap_count"], 0)
        self.assertFalse(got["production_deployment_authorized"])
        self.assertEqual(got["decision"], "HOLD")

    def test_readwrite_ssd_ancestor_denied(self):
        data = copy.deepcopy(self.record)
        data["Mounts"][0] = self.mount(self.root / "ssd", "/media/scott/SSD_4TB", True)
        got = self.check(data)
        self.assertEqual(got["protected_overlap_count"], 1)
        self.assertEqual(got["protected_overlap_writable_count"], 1)
        self.assertIn("protected_evidence_writable_alias", got["violations"])

    def test_canonical_symlink_ancestor_denied(self):
        alias = self.root / "knowledge-alias"
        alias.symlink_to(self.protected.parent, target_is_directory=True)
        data = copy.deepcopy(self.record)
        data["Mounts"][0] = self.mount(alias, "/root/knowledge", True)
        got = self.check(data)
        self.assertEqual(got["protected_overlap_count"], 1)
        self.assertEqual(got["protected_overlap_writable_count"], 1)

    def test_readonly_ancestor_still_exposes_protected_data(self):
        data = copy.deepcopy(self.record)
        data["Mounts"][0] = self.mount(self.root / "ssd", "/research/knowledge", False)
        got = self.check(data)
        self.assertIn("protected_evidence_readable_alias", got["violations"])
        self.assertEqual(got["protected_overlap_readonly_count"], 1)
        self.assertFalse(got["protected_evidence_path_exclusion_pass"])

    def test_protected_subtree_denied(self):
        part = self.protected / "child"
        part.mkdir()
        data = copy.deepcopy(self.record)
        data["Mounts"][0] = self.mount(part, "/research/knowledge", False)
        self.assertFalse(self.check(data)["protected_evidence_path_exclusion_pass"])

    def test_two_independent_aliases_counted(self):
        alias = self.root / "alias"
        alias.symlink_to(self.protected.parent, target_is_directory=True)
        data = {"Mounts": [
            self.mount(self.root / "ssd", "/mnt/ssd", True),
            self.mount(alias, "/root/knowledge", True),
        ]}
        got = self.check(data)
        self.assertEqual(got["protected_overlap_count"], 2)
        self.assertEqual(got["protected_overlap_writable_count"], 2)

    def test_unknown_source_denied(self):
        data = copy.deepcopy(self.record)
        data["Mounts"][0]["Source"] = str(self.root / "missing")
        got = self.check(data)
        self.assertIn("mount_source_unavailable", got["violations"])
        self.assertEqual(got["unknown_mount_count"], 1)

    def test_volume_and_duplicate_destination_denied(self):
        data = copy.deepcopy(self.record)
        data["Mounts"][0]["Type"] = "volume"
        self.assertFalse(self.check(data)["protected_evidence_path_exclusion_pass"])
        data = copy.deepcopy(self.record)
        data["Mounts"][1]["Destination"] = data["Mounts"][0]["Destination"]
        self.assertFalse(self.check(data)["protected_evidence_path_exclusion_pass"])

    def test_protected_root_must_exist(self):
        out = guard.audit(self.record, protected_roots=(str(self.root / "absent"),))
        self.assertIn("protected_root_unavailable", out["violations"])

    def test_empty_malformed_and_forged_record_denied(self):
        for record in (None, {}, {"Mounts": []}, {"Mounts": [True]},
                       {"Mounts": [{"Source": str(self.approved), "Type": "bind",
                                    "Destination": "/workspace", "RW": 1}]}):
            with self.subTest(record=str(record)[:25]):
                result = guard.audit(record, protected_roots=(str(self.protected),))
                self.assertFalse(result["protected_evidence_path_exclusion_pass"])

    def test_default_mode_does_not_touch_docker(self):
        with patch.object(guard.subprocess, "run") as sub:
            sample = guard.audit(None, protected_roots=(str(self.protected),))
        sub.assert_not_called()
        self.assertFalse(sample["protected_evidence_path_exclusion_pass"])

    def test_report_does_not_leak_host_paths(self):
        data = copy.deepcopy(self.record)
        data["Mounts"][0] = self.mount(self.root / "ssd", "/media/scott/SSD_4TB", True)
        report = str(self.check(data))
        self.assertNotIn(str(self.root), report)
        self.assertNotIn("/media/scott", report)
        self.assertNotIn("FLEET_ROUTER_BEARER_TOKEN", report)


if __name__ == "__main__":
    unittest.main()
