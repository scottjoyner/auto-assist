"""Synthetic-only Hermes curated knowledge manifest/custody negative tests.

No real NAS path is traversed, accessed, scanned, copied or modified.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "deploy/hermes-rootless/verify_curated_knowledge.py"
SPEC = importlib.util.spec_from_file_location("verify_curated_knowledge", MODULE)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

class CuratedSyntheticKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(
            prefix="og-hermes-knowledge-", dir="/tmp")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.root.chmod(0o700)
        (self.root / "notes").mkdir(mode=0o700)
        self.payloads = {
            "readme.txt": b"synthetic fixture only\n",
            "notes/guide.md": b"not a real model corpus\n",
        }
        for name, blob in self.payloads.items():
            (self.root / name).write_bytes(blob)
        self.manifest = {
            "schema": 1,
            "kind": "synthetic-hermes-curated-v1",
            "generation": "synthetic-20261021",
            "files": [
                {"path": p, "size": len(blob),
                 "sha256": hashlib.sha256(blob).hexdigest()}
                for p, blob in sorted(self.payloads.items())
            ],
        }

    def check(self, manifest=None, root=None):
        return guard.audit(root if root is not None else self.root,
                           self.manifest if manifest is None else manifest)

    def denied(self, result, violation=None):
        self.assertFalse(result["synthetic_curated_fixture_verified"], result)
        self.assertFalse(result["production_knowledge_mount_authorized"])
        self.assertEqual(result["decision"], "HOLD")
        if violation:
            self.assertIn(violation, result["violations"])

    def test_valid_exact_manifest_is_synthetic_not_release(self):
        result = self.check()
        self.assertTrue(result["synthetic_curated_fixture_verified"], result)
        self.assertEqual(result["files_verified"], 2)
        self.assertEqual(result["logical_bytes_verified"],
                         sum(len(x) for x in self.payloads.values()))
        self.assertFalse(result["independently_signed_corpus_authority"])
        self.assertFalse(result["source_is_real_nas"])
        self.assertFalse(result["production_knowledge_mount_authorized"])
        self.assertEqual(result["decision"], "HOLD")

    def test_no_input_causes_no_source_inspection(self):
        with patch.object(guard.os, "scandir") as scandir:
            result = guard.audit()
        scandir.assert_not_called()
        self.denied(result, "fixture_not_provided")

    def test_canonical_nas_and_legacy_nas_denied_before_scanning(self):
        with patch.object(guard.os, "scandir") as scandir:
            for path in ("/nas", "/media/scott/NAS5", "/home/scott/git",
                         "/home/scott/.ssh"):
                with self.subTest(path=path):
                    self.denied(self.check(root=path))
        scandir.assert_not_called()

    def test_unsafe_absolute_and_traversal_paths(self):
        for name in ("/nas/private.txt", "../secret.txt", "notes/../readme.txt",
                     "notes//guide.md", "notes/../../etc/passwd",
                     "notes/.env", "watchdog-marker.txt",
                     "notes/dashcam-log.txt", ".hidden"):
            with self.subTest(name=name):
                manifest = json.loads(json.dumps(self.manifest))
                manifest["files"][0]["path"] = name
                self.denied(self.check(manifest), "manifest_path_invalid")

    def test_unsigned_real_manifest_is_never_synthetic_admitted(self):
        manifest = dict(self.manifest)
        manifest["kind"] = "real-nas-curated-v1"
        self.denied(self.check(manifest), "production_manifest_denied")

    def test_wrong_hash_and_modified_content(self):
        with self.subTest("wrong manifest digest"):
            manifest = json.loads(json.dumps(self.manifest))
            manifest["files"][0]["sha256"] = "f"*64
            self.denied(self.check(manifest), "content_digest_mismatch")
        with self.subTest("unexpected mutation"):
            (self.root / "readme.txt").write_bytes(b"changed\n")
            self.denied(self.check(), "file_size_mismatch")

    def test_unlisted_extra_file_denied(self):
        (self.root / "notes/extra.md").write_bytes(b"synthetic extra")
        self.denied(self.check(), "unexpected_source_file_denied")

    def test_unlisted_empty_directory_denied(self):
        (self.root / "unlisted").mkdir()
        self.denied(self.check(), "unlisted_directory_denied")

    def test_missing_file_denied(self):
        (self.root / "readme.txt").unlink()
        self.denied(self.check(), "manifest_missing_file")

    def test_symlinked_file_denied(self):
        (self.root / "readme.txt").unlink()
        (self.root / "readme.txt").symlink_to(
            self.root / "notes/guide.md")
        self.denied(self.check(), "symlink_or_cross_device_denied")

    def test_symlinked_directory_denied(self):
        (self.root / "notes/guide.md").unlink()
        (self.root / "notes").rmdir()
        (self.root / "notes").symlink_to(self.root)
        self.denied(self.check(), "symlink_or_cross_device_denied")

    def test_hardlink_of_existing_file_denied(self):
        (self.root / "readme.txt").unlink()
        os.link(self.root / "notes/guide.md", self.root / "readme.txt")
        self.denied(self.check(), "hardlink_or_special_file_denied")

    def test_weakened_scratch_permissions_denied(self):
        self.root.chmod(0o755)
        self.addCleanup(lambda: self.root.chmod(0o700))
        self.denied(self.check(), "untrusted_fixture_permissions")

    def test_wrong_temporary_prefix_denied(self):
        with tempfile.TemporaryDirectory(prefix="arbitrary-host-",
                                         dir="/tmp") as other:
            self.denied(self.check(root=other), "untrusted_fixture_root")

    def test_duplicate_path_denied(self):
        manifest = json.loads(json.dumps(self.manifest))
        manifest["files"].append(manifest["files"][0])
        self.denied(self.check(manifest), "manifest_path_invalid")

    def test_too_many_files_denied(self):
        manifest = json.loads(json.dumps(self.manifest))
        manifest["files"] *= 10
        self.denied(self.check(manifest), "manifest_count_invalid")

    def test_file_size_limit_denied(self):
        manifest = json.loads(json.dumps(self.manifest))
        manifest["files"][0]["size"] = guard.MAX_FILE_BYTES + 1
        self.denied(self.check(manifest), "manifest_file_size_invalid")

    def test_hidden_unexpected_environment_file_denied(self):
        (self.root / ".env").write_text("SYNTHETIC_TOKEN=DO_NOT_PERSIST")
        self.denied(self.check(), "unexpected_source_file_denied")

    def test_never_echo_synthetic_paths_or_secret_values(self):
        result = self.check()
        report = json.dumps(result)
        self.assertNotIn(str(self.root), report)
        self.assertNotIn("/nas", report)
        self.assertNotIn("SYNTHETIC_TOKEN", report)

    def test_release_authority_is_hardcoded_false(self):
        manifest = json.loads(json.dumps(self.manifest))
        manifest["production_deployment_authorized"] = True
        self.denied(self.check(manifest), "manifest_schema_invalid")

if __name__ == "__main__":
    unittest.main()
