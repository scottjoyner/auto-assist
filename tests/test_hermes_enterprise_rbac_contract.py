"""Research-only Enterprise DB authorization canary safety invariants."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"tests/hermes_task_broker_rbac_disposable_neo4j.py"
WORKFLOW=ROOT/".github/workflows/hermes-enterprise-rbac.yml"


class RBACCanaryContractTests(unittest.TestCase):
    def test_disposable_only_guard_and_loopback_are_mandatory(self):
        raw=SOURCE.read_text()
        self.assertIn('HERMES_ENTERPRISE_RBAC_CANARY',raw)
        self.assertIn('synthetic-ci-only',raw)
        self.assertIn('assert "NEO4J_URI" not in os.environ',raw)
        self.assertIn('assert "NEO4J_PASSWORD" not in os.environ',raw)
        self.assertIn('assert "NEO4J_AUTH" not in os.environ',raw)
        self.assertIn('bolt://127.0.0.1:7687',raw)
        self.assertIn('HERMES_RBAC_EPHEMERAL_ADMIN_PASSWORD',raw)
        self.assertNotIn('Tailscale',raw)

    def test_reader_roles_are_distinct_from_broker_role(self):
        raw=SOURCE.read_text()
        self.assertIn('AS COPY OF reader',raw)
        self.assertIn('GRANT ROLE {READER_ROLE} TO {name}',raw)
        self.assertIn('GRANT ROLE {ROLE} TO {BROKER}',raw)
        self.assertNotIn('AS COPY OF admin',raw)
        self.assertNotIn('AS COPY OF editor',raw)
        self.assertIn('SET PROPERTY {{status}} ON GRAPH neo4j NODES Task',raw)
        self.assertIn('broker_Task_delete_denied',raw)

    def test_live_cred_rotations_absent_and_no_production_hook(self):
        raw=SOURCE.read_text()
        tree=ast.parse(raw)
        self.assertTrue(tree)
        for path in (ROOT/"src/assistx").rglob("*.py"):
            self.assertNotIn("hermes_task_broker_rbac_disposable_neo4j",path.read_text())

    def test_enterprise_workflow_is_separate_from_production(self):
        s=WORKFLOW.read_text()
        self.assertIn('neo4j:5.26-enterprise',s)
        self.assertIn('NEO4J_ACCEPT_LICENSE_AGREEMENT=eval',s)
        self.assertIn('HERMES_ENTERPRISE_RBAC_CANARY: synthetic-ci-only',s)
        self.assertIn('run: python tests/hermes_task_broker_rbac_disposable_neo4j.py',s)
        self.assertIn('secrets.token_urlsafe',s)
        self.assertNotIn('synthetic-only-admin-not-a-secret',s)
        self.assertNotIn("docker compose up",s)
        self.assertNotIn("kubectl",s)

if __name__=="__main__":
    unittest.main()
