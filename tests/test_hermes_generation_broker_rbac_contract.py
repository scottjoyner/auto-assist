"""No production exposure from Enterprise generation/broker canary."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"tests/hermes_broker_generation_rbac_disposable_neo4j.py"
WF=ROOT/".github/workflows/hermes-enterprise-rbac.yml"


class ScopedBrokerWitnessTests(unittest.TestCase):
    def test_localhost_only_and_disposable_credential_marker(self):
        content=SRC.read_text()
        self.assertIn('URI="bolt://127.0.0.1:7687"',content)
        self.assertIn('HERMES_ENTERPRISE_RBAC_CANARY',content)
        self.assertIn('synthetic-ci-only',content)
        self.assertIn('HERMES_RBAC_EPHEMERAL_ADMIN_PASSWORD',content)
        self.assertIn('assert "NEO4J_URI" not in os.environ',content)
        self.assertIn('assert "NEO4J_PASSWORD" not in os.environ',content)
        self.assertIn('secrets.token_urlsafe',content)
        self.assertNotIn('Tailscale',content)
        ast.parse(content)

    def test_restricted_role_uses_task_properties_and_ledger_only(self):
        content=SRC.read_text()
        self.assertIn('GRANT SHOW CONSTRAINT ON DATABASE neo4j',content)
        self.assertIn('SET PROPERTY ',content)
        self.assertIn('ON GRAPH neo4j NODES Task TO',content)
        self.assertIn('ON GRAPH neo4j NODES HermesSourceGeneration TO',content)
        self.assertIn('grant',content.lower())
        self.assertIn('narrow_broker_unlisted_Task_field_denied',content)
        self.assertIn('narrow_broker_Task_delete_denied',content)
        self.assertNotIn('AS COPY OF admin',content)
        self.assertNotIn('AS COPY OF editor',content)

    def test_enrolled_operations_and_explicit_bypass_hard_hold(self):
        content=SRC.read_text()
        self.assertIn('broker.dispatch_candidate_command(',content)
        self.assertIn('"operation":"heartbeat"',content)
        self.assertIn('"operation":"complete"',content)
        self.assertIn('command_surface_raw_database_query_denied_before_session',content)
        self.assertIn('source_generation_writers_complete',content)
        self.assertIn('broker_credential_still_bypasses_generation_PROTOCOL_HOLD',content)
        self.assertIn('ALL_WRITERS_GENERATION_FENCED=FALSE',content)
        self.assertIn('PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD',content)

    def test_workflow_isolated_and_before_cleanup(self):
        yml=WF.read_text()
        self.assertIn('neo4j:5.26-enterprise',yml)
        self.assertIn('print("::add-mask::"+value, flush=True)',yml)
        self.assertIn('run: python tests/hermes_broker_generation_rbac_disposable_neo4j.py',yml)
        self.assertLess(yml.index('run: python tests/hermes_broker_generation_rbac_disposable_neo4j.py'),
                        yml.index('- name: Remove isolated Enterprise DB'))
        self.assertNotIn('docker compose up',yml)

    def test_production_code_never_imports_broker_witness(self):
        for f in (ROOT/"src/assistx").rglob("*.py"):
            self.assertNotIn("hermes_broker_generation_rbac_disposable_neo4j",
                             f.read_text(), f)

if __name__=="__main__":
    unittest.main()
