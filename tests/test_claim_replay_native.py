"""Claim retries must not resurrect stale leases or cross owner boundaries."""
import unittest
from unittest.mock import Mock, MagicMock
from assistx.neo4j_client import Neo4jClient

class ClaimReplayTests(unittest.TestCase):
    def fixture(self, **changes):
        task={"status":"CLAIMED","claimed_by":"agent-a","agent_session_id":"session-a","claim_id":"synthetic-key","lease_expires_at_ts":20000}
        task.update(changes)
        s=Mock()
        s.run.return_value.single.return_value={"t":task,"lease_current":task["lease_expires_at_ts"]>10000}
        n=Neo4jClient.__new__(Neo4jClient)
        n._session=MagicMock()
        n._session.return_value.__enter__.return_value=s
        return n,s
    def claim(self,n,**kw):
        return n.claim_task("synthetic-task","agent-a",session_id=kw.get("session_id","session-a"),idempotency_key="synthetic-key")
    def assert_denied(self,**changes):
        n,s=self.fixture(**changes)
        r=self.claim(n)
        self.assertFalse(r["claimed"])
        self.assertNotIn("task",r)
        self.assertEqual(s.run.call_count,1)
    def test_current_owner_can_retry(self):
        n,s=self.fixture()
        r=self.claim(n)
        self.assertTrue(r["claimed"]);self.assertTrue(r["idempotent"])
        self.assertEqual(s.run.call_count,1)
    def test_other_owner_rejected(self):self.assert_denied(claimed_by="agent-b")
    def test_other_session_rejected(self):self.assert_denied(agent_session_id="session-b")
    def test_ready_claim_metadata_not_replayed(self):self.assert_denied(status="READY")
    def test_completed_claim_not_replayed(self):self.assert_denied(status="DONE")
    def test_expired_lease_not_replayed(self):self.assert_denied(lease_expires_at_ts=9999)
    def test_running_claim_can_retry(self):
        n,s=self.fixture(status="RUNNING")
        self.assertTrue(self.claim(n)["claimed"])
    def test_missing_lease_witness_fails_closed(self):
        n,s=self.fixture()
        s.run.return_value.single.return_value.pop("lease_current")
        self.assertFalse(self.claim(n)["claimed"])
