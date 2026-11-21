"""Static and no-op admission checks for the optional Docker-only harness."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "endpoint_identity_candidate_disposable.py"


class DisposableHarnessGuardTests(unittest.TestCase):
    def test_no_argument_does_not_start_docker_or_authorize_release(self):
        # Never run --run-disposable from hosted CI. This tests only default deny.
        outcome = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True, text=True,
            timeout=8,
        )
        self.assertEqual(outcome.returncode, 2)
        response = json.loads(outcome.stdout)
        self.assertEqual(
            response["status"], "HOLD_EXPLICIT_DISPOSABLE_OPT_IN_REQUIRED",
        )
        self.assertFalse(response["docker_commands_executed"])
        self.assertFalse(response["production_release_authorized"])

    def test_program_is_explicitly_opt_in_and_unwired(self):
        script = SCRIPT.read_text()
        self.assertIn("--run-disposable", script)
        self.assertIn("if not args.run_disposable:", script)
        self.assertIn("source_unwired_from_production", script)
        self.assertIn("production_graph_mutated", script)
        self.assertIn("production_release_authorized", script)
        self.assertNotIn("--privileged", script)
        self.assertNotIn("docker compose", script)

    def test_isolated_graph_has_no_published_ports_or_production_mounts(self):
        script = SCRIPT.read_text()
        for required in (
            '"--internal"', '"--rm"', '"--network"', '"--memory"',
            '"--pids-limit"', '"no-new-privileges"',
        ):
            self.assertIn(required, script)
        # No host port/volume published in the fixed container invocation.
        for forbidden in ('"--publish"', '"--volume"', '"--mount"', '"--privileged"'):
            self.assertNotIn(forbidden, script)
        self.assertIn("container_meta['HostConfig']", script.replace('"', "'"))
        self.assertIn("container_removed", script)
        self.assertIn("network_removed", script)

    def test_both_owner_races_are_represented(self):
        script = SCRIPT.read_text()
        self.assertIn("concurrent_same_owner_no_duplicate_or_edge_fanout", script)
        self.assertIn("concurrent_distinct_owners_fail_closed_after_lock", script)
        self.assertIn("concurrent_matching_service_and_model_writers_share_one_node", script)
        self.assertIn("concurrent_generic_service_vs_model_does_not_retype", script)
        self.assertIn("Barrier(2)", script)
        self.assertIn("ThreadPoolExecutor(max_workers=2)", script)

    def test_module_does_not_run_docker_at_import(self):
        tree = ast.parse(SCRIPT.read_text())
        top_call_names = []
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                top_call_names.append(ast.unparse(node.value.func))
        self.assertNotIn("run", top_call_names)
        self.assertNotIn("subprocess.run", top_call_names)

    def test_source_guard_protects_legacy_writer(self):
        production = (ROOT / "src/assistx/swarm_core.py").read_text()
        self.assertNotIn("endpoint_identity_candidate", production)
        self.assertNotIn("candidate_register_model_endpoint(", production)


if __name__ == "__main__":
    unittest.main()
