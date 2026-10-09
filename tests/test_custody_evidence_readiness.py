"""No secrets, no network: fail-closed custody evidence contract."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "custody_evidence_readiness.py"
spec = importlib.util.spec_from_file_location("custody_evidence_readiness", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def checkpoint(states=None):
    states = states or {}
    return {
        "schema": mod.SCHEMA,
        "checkpoints": {
            stage: {"state": states.get(stage, "pending"),
                    "reference": ("CUSTODY-TEST-123456" if states.get(stage) == "submitted" else None)}
            for stage in mod.STAGES
        },
    }


class CustodyContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "work"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)

    def track(self, path):
        f = self.repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("SYNTHETIC-PLACEHOLDER-NOT-A-SECRET")
        subprocess.run(["git", "-C", str(self.repo), "add", "-f", "--", path], check=True)

    def test_empty_index_is_hold(self):
        o = mod.inspect(self.repo)
        self.assertEqual(o["tracked_env_variant_count"], 0)
        self.assertEqual(o["status"], "HOLD")
        self.assertFalse(o["production_authorized"])

    def test_sensitive_backup_filename_is_counted(self):
        self.track(".env.bak-sync-20261006T051130Z")
        o = mod.inspect(self.repo)
        self.assertEqual(o["tracked_env_variant_count"], 1)
        self.assertIn("tracked_environment_variants_present", o["reasons"])
        self.assertNotIn(".env.bak-sync", json.dumps(o))

    def test_extra_dot_env_example_is_not_allowed(self):
        self.track(".env.reconciliation.example")
        self.assertEqual(mod.inspect(self.repo)["tracked_env_variant_count"], 1)

    def test_two_explicit_templates_are_allowed(self):
        self.track(".env.example")
        self.track(".env.kipnerter-gateway.example")
        self.assertEqual(mod.inspect(self.repo)["tracked_env_variant_count"], 0)

    def test_archived_remediated_template_is_allowed(self):
        self.track(mod.ARCHIVED)
        self.assertEqual(mod.inspect(self.repo)["tracked_env_variant_count"], 0)

    def test_nested_env_variant_is_counted(self):
        self.track("nested/deep/.env.local")
        self.assertEqual(mod.inspect(self.repo)["tracked_env_variant_count"], 1)

    def test_all_claims_cannot_authorize(self):
        e = checkpoint(dict.fromkeys(mod.STAGES, "submitted"))
        o = mod.inspect(self.repo, e)
        self.assertEqual(o["owner_checkpoint_claims_submitted"], len(mod.STAGES))
        self.assertFalse(o["merge_authorized"])
        self.assertIn("all_checkpoint_claims_still_unverified", o["reasons"])

    def test_missing_stage_denied(self):
        e = checkpoint()
        e["checkpoints"].pop("owner_security_signoff")
        self.assertIn("incomplete_checkpoint_matrix", mod.inspect(self.repo,e)["reasons"])

    def test_unknown_fields_do_not_echo(self):
        e = checkpoint()
        e["exfiltration_key"]="DO-NOT-PRINT-THIS-SECRET-VALUE"
        o=mod.inspect(self.repo,e)
        self.assertIn("invalid_checkpoint_schema",o["reasons"])
        self.assertNotIn("DO-NOT-PRINT",json.dumps(o))

    def test_malformed_state_types_deny_not_crash(self):
        for bad in ([], {"state":"submitted"}, 1, True, None):
            with self.subTest(kind=type(bad).__name__):
                e=checkpoint()
                e["checkpoints"]["exposure_containment"]["state"]=bad
                o=mod.inspect(self.repo,e)
                self.assertEqual(o["status"],"HOLD")
                self.assertIn("invalid_checkpoint_state",o["reasons"])

    def test_reference_must_be_opaque(self):
        e=checkpoint({"exposure_containment":"submitted"})
        e["checkpoints"]["exposure_containment"]["reference"]="https://example.com/?credential=PRIVATE"
        o=mod.inspect(self.repo,e)
        self.assertIn("invalid_opaque_reference",o["reasons"])
        self.assertNotIn("PRIVATE",json.dumps(o))

    def test_late_claim_denied_out_of_order(self):
        e=checkpoint({"owner_security_signoff":"submitted"})
        o=mod.inspect(self.repo,e)
        self.assertIn("out_of_order_checkpoint_claim",o["reasons"])

    def test_invalid_historical_revision_denied_without_execution(self):
        for bad in ("HEAD", "main", "deadbeef", "--help", "../config",
                    "a" * 40 + " --", "", None, 123):
            with self.subTest(revision=str(bad)[:20]):
                o=mod.inspect(self.repo, historical_revision=bad)
                self.assertEqual(o["status"],"HOLD")
                self.assertFalse(o["pinned_historical_revision_checked"])
                self.assertIsNone(o["historical_env_variant_count"])

    def test_history_snapshot_remains_relevant_after_index_cleanup(self):
        self.track(".env.local")
        subprocess.run([
            "git", "-C", str(self.repo), "-c", "user.name=Research Fixture",
            "-c", "user.email=research@invalid.example", "commit",
            "-q", "-m", "synthetic history test",
        ], check=True)
        old = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=True,
        ).stdout.strip()
        # Keep the historical tree intact but clean the *current index*.
        subprocess.run(
            ["git", "-C", str(self.repo), "rm", "-q", "--", ".env.local"], check=True,
        )
        o=mod.inspect(self.repo, historical_revision=old)
        self.assertEqual(o["tracked_env_variant_count"], 0)
        self.assertEqual(o["historical_env_variant_count"], 1)
        self.assertTrue(o["pinned_historical_revision_checked"])
        self.assertIn("historical_revision_contains_env_variants", o["reasons"])
        self.assertFalse(o["merge_authorized"])

    def test_historical_clean_commit_is_not_all_history_attestation(self):
        self.track("src/placeholder.txt")
        subprocess.run([
            "git", "-C", str(self.repo), "-c", "user.name=Research Fixture",
            "-c", "user.email=research@invalid.example", "commit",
            "-q", "-m", "clean synthetic snapshot",
        ], check=True)
        old = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=True,
        ).stdout.strip()
        o=mod.inspect(self.repo, historical_revision=old)
        self.assertEqual(o["historical_env_variant_count"], 0)
        self.assertIn("selected_history_snapshot_clean_not_comprehensive", o["reasons"])
        self.assertFalse(o["production_authorized"])

    def test_unavailable_historical_git_tree_holds(self):
        o=mod.inspect(self.repo, historical_revision="f" * 40)
        self.assertEqual(o["status"],"HOLD")
        self.assertIn("historical_index_unavailable", o["reasons"])

    def test_git_unavailable_fails_closed(self):
        o=mod.inspect(self.repo/"not-a-repo")
        self.assertIn("index_unavailable",o["reasons"])
        self.assertIsNone(o["tracked_env_variant_count"])

    def test_receipt_hash_is_deterministic(self):
        a=mod.inspect(self.repo)
        b=mod.inspect(self.repo)
        self.assertEqual(a["observation_sha256"],b["observation_sha256"])

    def test_cli_exit_nonzero_when_claims_good(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"claims.json"
            p.write_text(json.dumps(checkpoint(dict.fromkeys(mod.STAGES, "submitted"))))
            r=subprocess.run([sys.executable,str(SCRIPT),"--repo",str(self.repo),
                              "--checkpoint-json",str(p)],capture_output=True,text=True)
            self.assertEqual(r.returncode,1)
            self.assertEqual(json.loads(r.stdout)["status"],"HOLD")


if __name__ == "__main__":
    unittest.main()
