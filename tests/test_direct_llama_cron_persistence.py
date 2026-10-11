"""Offline cron-supervisor provenance tests: no crontab writes or commands."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_direct_llama_startup",
    ROOT / "scripts" / "audit_direct_llama_startup.py",
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


class TestCronPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.embedding = self.home / "embedding-server-persist.sh"
        self.embedding.write_text(
            "#!/bin/sh\n# persistent llama-server at port 1235\n"
            "exec /opt/bin/llama-server --port 1235 --model \"$PRIVATE_MODEL\"\n"
        )
        self.other = self.home / "inference-server-persist.sh"
        self.other.write_text(
            "#!/bin/sh\nexec /opt/bin/llama-server --port 4567\n"
        )

    def entries(self, *, extra=""):
        return (
            f"@reboot {self.embedding} >>/tmp/log 2>&1\n"
            f"*/5 * * * * {self.embedding} >/dev/null 2>&1\n"
            f"@reboot {self.other}\n"
            f"*/10 * * * * {self.other}\n"
            + extra
        )

    def inspect(self, crontab=None, **updates):
        kwargs = {
            "home": self.home, "listener_port": 1235,
            "cgroup": "0::/system.slice/cron.service",
        }
        kwargs.update(updates)
        return M.audit_text(
            self.entries() if crontab is None else crontab,
            **kwargs,
        )

    def test_reboot_and_periodic_supervisors_identified(self):
        r = self.inspect()
        self.assertEqual(r["status"], "BLOCKED_CRON_PERSISTENCE_CANDIDATE")
        self.assertEqual(r["candidate_reboot_entries"], 1)
        self.assertEqual(r["candidate_periodic_entries"], 1)
        self.assertEqual(r["candidate_launcher_entries"], 2)
        self.assertEqual(r["active_current_user_cron_entries"], 4)
        self.assertTrue(r["observed_service_cgroup_is_cron"])

    def test_observation_never_grants_any_authorization(self):
        r = self.inspect()
        for field in (
            "proven_current_process_origin", "dependencies_completely_inventoried",
            "tailnet_access_restricted", "operator_change_approved",
            "can_change_cron", "can_change_listener", "can_approve",
            "can_dispatch",
        ):
            self.assertIs(r[field], False)

    def test_no_full_commands_paths_or_model_secrets_in_output(self):
        raw = json.dumps(self.inspect())
        self.assertNotIn(str(self.home), raw)
        self.assertNotIn("$PRIVATE_MODEL", raw)
        self.assertNotIn("/opt/bin/llama-server", raw)
        self.assertNotIn("/tmp/log", raw)
        self.assertNotIn("exec ", raw)

    def test_other_model_supervisor_does_not_count_as_same_port(self):
        r = self.inspect()
        self.assertEqual(
            [x["launcher_basename"] for x in r["candidate_launchers"]],
            ["embedding-server-persist.sh", "embedding-server-persist.sh"],
        )

    def test_other_periodic_and_reboot_llama_launcher_is_latent_only(self):
        self.other.write_text(
            "#!/bin/sh\n"
            "exec /opt/bin/llama-server --host 0.0.0.0 --port 4567\n"
        )
        result = self.inspect()
        self.assertEqual(result["candidate_launcher_entries"], 2)
        self.assertEqual(result["other_llama_launcher_entries"], 2)
        self.assertFalse(result["other_launcher_current_process_proven"])
        for row in result["other_llama_launchers"]:
            self.assertEqual(row["launcher_basename"], "inference-server-persist.sh")
            self.assertTrue(row["wildcard_host_text_found"])
            self.assertFalse(row["current_process_running_proven"])
            self.assertFalse(row["listener_exposure_proven"])
            self.assertFalse(row["can_approve"])
            self.assertFalse(row["can_dispatch"])

    def test_other_launcher_does_not_mint_active_exposure(self):
        r = self.inspect(
            crontab=f"@reboot {self.other}\n"
        )
        self.assertFalse(r["relaunch_persistence_possible"])
        self.assertEqual(r["other_llama_launcher_entries"], 1)
        self.assertFalse(r["other_llama_launchers"][0]["listener_exposure_proven"])
        self.assertEqual(r["status"], "BLOCKED_LAUNCH_SOURCE_UNRESOLVED")

    def test_comment_only_cron_excluded(self):
        t = "# " + f"@reboot {self.embedding}\n"
        r = self.inspect(crontab=t)
        self.assertEqual(r["candidate_launcher_entries"], 0)
        self.assertEqual(r["status"], "BLOCKED_LAUNCH_SOURCE_UNRESOLVED")

    def test_unknown_cron_origin_remains_blocked(self):
        r = self.inspect(crontab="@reboot /usr/bin/true\n", cgroup="user.slice")
        self.assertFalse(r["relaunch_persistence_possible"])
        self.assertFalse(r["can_dispatch"])

    def test_symlinked_script_cannot_be_inspected(self):
        link = self.home / "wrapper.sh"
        link.symlink_to(self.embedding)
        r = self.inspect(crontab=f"@reboot {link}\n")
        self.assertEqual(r["candidate_launcher_entries"], 0)
        self.assertEqual(r["undecodable_or_unreadable_candidates"], 1)

    def test_outside_home_script_never_inspected(self):
        outside = Path("/etc/shadow")
        r = self.inspect(crontab=f"@reboot {outside}\n")
        self.assertEqual(r["candidate_launcher_entries"], 0)
        self.assertFalse(r["can_dispatch"])

    def test_large_script_refused(self):
        self.embedding.write_bytes(b"x" * (M.MAX_SCRIPT_BYTES + 1))
        r = self.inspect()
        self.assertEqual(r["candidate_launcher_entries"], 0)
        self.assertEqual(r["undecodable_or_unreadable_candidates"], 2)

    def test_malformed_crontab_does_not_become_permission(self):
        r = self.inspect(crontab="*/5 malformed\n")
        self.assertEqual(r["status"], "BLOCKED_LAUNCH_SOURCE_UNRESOLVED")
        self.assertGreater(r["undecodable_or_unreadable_candidates"], 0)

    def test_bad_port_rejected(self):
        for port in (0, True, -1, 65536):
            with self.subTest(port=port):
                with self.assertRaises(ValueError):
                    self.inspect(listener_port=port)

    def test_localhost_literal_in_healthcheck_does_not_prove_bind(self):
        self.embedding.write_text(
            "#!/bin/sh\n"
            "curl -fsS http://127.0.0.1:1235/health >/dev/null\n"
            "exec /opt/bin/llama-server --host 0.0.0.0 --port 1235\n"
        )
        r = self.inspect()
        self.assertEqual(r["status"], "BLOCKED_CRON_PERSISTENCE_CANDIDATE")
        self.assertTrue(r["candidate_launchers"][0]["mentions_loopback_literal"])
        self.assertFalse(r["candidate_launchers"][0]["declared_host_loopback_proven"])
        self.assertFalse(r["can_change_listener"])

    def test_oversized_crontab_rejected(self):
        with self.assertRaises(ValueError):
            self.inspect(crontab="x" * (M.MAX_CRONTAB_BYTES + 1))

    def test_periodic_only_restart_is_still_persistence(self):
        r = self.inspect(crontab=f"*/2 * * * * {self.embedding}\n")
        self.assertEqual(r["candidate_periodic_entries"], 1)
        self.assertEqual(r["candidate_reboot_entries"], 0)

    def test_hash_fingerprint_changes_when_script_changes(self):
        old = self.inspect()["candidate_launchers"][0]["launcher_sha256_prefix"]
        self.embedding.write_text(self.embedding.read_text() + "\n# changed\n")
        new = self.inspect()["candidate_launchers"][0]["launcher_sha256_prefix"]
        self.assertNotEqual(old, new)


if __name__ == "__main__":
    unittest.main()
