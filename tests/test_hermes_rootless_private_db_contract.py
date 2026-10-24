"""Offline guardrails for disposable private-credential integration witness."""
from __future__ import annotations
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"tests/hermes_rootless_broker_credential_db_disposable.py"
WF=ROOT/".github/workflows/hermes-enterprise-rbac.yml"

class RootlessCredentialCIContract(unittest.TestCase):
    def test_ci_only_and_hardcoded_local_endpoint(self):
        s=SOURCE.read_text()
        self.assertIn('HERMES_ROOTLESS_DB_CI',s)
        self.assertIn('synthetic-only',s)
        self.assertIn('assert "NEO4J_URI" not in os.environ',s)
        self.assertIn('assert "NEO4J_PASSWORD" not in os.environ',s)
        self.assertIn('URI="bolt://127.0.0.1:7687"',s)
        ast.parse(s)

    def test_secret_passed_only_through_anonymous_fd(self):
        s=SOURCE.read_text()
        self.assertIn('read_fd,write_fd=os.pipe()',s)
        self.assertIn('pass_fds=(read_fd,)',s)
        self.assertIn('os.write(write_fd,writer_pass.encode("ascii")',s)
        self.assertIn('os.read(secret_fd,256)',s)
        self.assertIn('secret.encode() not in environ',s)
        self.assertIn('secret.encode() not in cmdline',s)
        self.assertIn('HERMES_RBAC_EPHEMERAL_ADMIN_PASSWORD" not in os.environ',s)
        self.assertNotIn('NEO4J_AUTH=neo4j/',s)
        self.assertNotIn('writer_pass+"',s)

    def test_exact_os_and_database_denial(self):
        s=SOURCE.read_text()
        self.assertIn('preexec_fn=drop_to(broker_uid.pw_uid,broker_uid.pw_gid)',s)
        self.assertIn('reader direct Task write allowed',s)
        self.assertIn('os.chmod(private,0o2710)',s)
        self.assertIn('stat.S_IMODE(meta.st_mode)==0o660',s)
        self.assertIn('socket_path,hb,broker_uid.pw_uid',s)
        self.assertIn('send_unix(socket_path,completion,client_uid.pw_uid',s)
        self.assertIn('get_state()==("DONE",2)',s)
        self.assertIn('HERMES_ROOTLESS_PRIVATE_DB_PIPE_CI_PASS=8',s)
        self.assertIn('emit_holding_replies=True',s)
        self.assertIn('PASS socket_receipts_qualified_HOLD_without_secrets',s)
        self.assertIn('PASS unprivileged_socket_client_cannot_read_broker_proc_memory_env',s)
        self.assertIn('assert unauthorized is None',s)
        self.assertIn('assert stale["accepted_source_transaction"] is False',s)
        self.assertIn('assert first["candidate_generation"]==1',s)
        self.assertIn('assert second["candidate_generation"]==2',s)
        self.assertIn('assert result["unconfirmed_socket_replies"]==0',s)
        self.assertIn('max_connections=8',s)
        self.assertIn('GRANT CREATE ON GRAPH neo4j NODES HermesSourceReceipt',s)
        self.assertIn('operation":"reconcile_heartbeat',s)
        self.assertIn('operation":"reconcile_complete',s)
        self.assertIn('rootless_socket_source_receipt_reconciles_without_rewriting',s)
        self.assertIn('assert prior["reconciled"] is True',s)
        self.assertIn('assert prior_done["reconciled"] is True',s)
        self.assertIn('assert receipt_state["generation"]==4',s)

        self.assertIn('PRODUCTION_TASKS_TOUCHED=0',s)

    def test_isolated_ci_no_live_runner_or_credentials_on_argv(self):
        y=WF.read_text()
        self.assertIn('tests/hermes_rootless_broker_credential_db_disposable.py',y)
        self.assertIn('HERMES_ROOTLESS_DB_CI=synthetic-only',y)
        self.assertIn('printf',y)
        self.assertIn('sudo -n env',y)
        self.assertLess(
            y.index('run: python tests/hermes_broker_generation_rbac_disposable_neo4j.py'),
            y.index('tests/hermes_rootless_broker_credential_db_disposable.py',y.index('steps:'))
        )
        self.assertLess(
            y.index('sudo -n env HERMES_ROOTLESS_DB_CI=synthetic-only'),
            y.index('- name: Remove isolated Enterprise DB')
        )

    def test_staged_rootless_process_includes_receipt_research_dependency(self):
        source=(ROOT/"tests/hermes_broker_rootless_process_disposable_linux.py").read_text()
        # Both the explicit module loader and the restricted CI source
        # staging copy must carry this dependency before the broker commands.
        self.assertEqual(source.count('"hermes_source_receipt_research"'),2)
        self.assertEqual(source.count('"hermes_broker_command_research"'),2)
        assert source.index('"hermes_source_receipt_research"') < source.index('"hermes_broker_command_research"')

    def test_no_live_imports_or_deploy_entrypoint(self):
        for p in (ROOT/"src/assistx").rglob("*.py"):
            self.assertNotIn("hermes_rootless_broker_credential_db_disposable",
                             p.read_text(),p)

if __name__=="__main__":
    unittest.main()
