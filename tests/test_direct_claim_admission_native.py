"""Synthetic transaction fixtures; no graph or service changes."""
import unittest
from unittest.mock import MagicMock, Mock, patch
from assistx.neo4j_client import Neo4jClient

class DirectAdmissionTests(unittest.TestCase):
    def fixture(self, mode="open", prior=None, missing=False):
        n=Neo4jClient.__new__(Neo4jClient)
        s=MagicMock();tx=MagicMock()
        n._session=MagicMock()
        n._session.return_value.__enter__.return_value=s
        s.begin_transaction.return_value.__enter__.return_value=tx
        def result(query,params=None):
            r=MagicMock()
            if "DirectClaimAdmission" in query:
                r.single.return_value=None if missing else {"mode":mode,"revision":4}
            elif "lease_current" in query:
                r.single.return_value=prior
            elif "SET t.status='CLAIMED'" in query:
                r.single.return_value={"t":{"status":"CLAIMED"}}
            else:
                r.single.return_value=None
            return r
        tx.run.side_effect=result
        return n,s,tx
    def claim(self,n,key=None):
        with patch.dict("os.environ",{"ASSISTX_DIRECT_CLAIM_FENCE_REQUIRED":"true"}):
            return n.claim_task("fixture-task","fixture-agent",session_id="fixture-session",idempotency_key=key)
    def test_closed_denies_before_task_mutation(self):
        n,s,tx=self.fixture("closed")
        r=self.claim(n)
        self.assertEqual(r,{"claimed":False,"reason":"direct_claim_admission_closed"})
        self.assertEqual(tx.run.call_count,1)
        s.run.assert_not_called()
        tx.commit.assert_called_once()
    def test_missing_gate_fails_closed(self):
        n,s,tx=self.fixture(missing=True)
        self.assertEqual(self.claim(n)["reason"],"direct_claim_admission_unavailable")
        self.assertEqual(tx.run.call_count,1)
        s.run.assert_not_called()
    def test_unknown_mode_fails_closed(self):
        n,s,tx=self.fixture("unknown")
        self.assertFalse(self.claim(n)["claimed"])
        self.assertEqual(tx.run.call_count,1)
    def test_open_claim_is_in_locked_transaction(self):
        n,s,tx=self.fixture()
        self.assertTrue(self.claim(n)["claimed"])
        queries=[c.args[0] for c in tx.run.call_args_list]
        self.assertIn("SET g.lock_sequence",queries[0])
        self.assertIn("SET t.status='CLAIMED'",queries[1])
        self.assertIn("AgentSession",queries[2])
        tx.commit.assert_called_once();s.run.assert_not_called()
    def test_closed_allows_current_owner_replay_only(self):
        prior={"t":{"status":"CLAIMED","claimed_by":"fixture-agent","agent_session_id":"fixture-session"},"lease_current":True}
        n,s,tx=self.fixture("closed",prior=prior)
        self.assertTrue(self.claim(n,"fixture-key")["idempotent"])
        self.assertEqual(tx.run.call_count,2)
    def test_closed_rejects_stale_replay(self):
        prior={"t":{"status":"READY","claimed_by":"fixture-agent","agent_session_id":"fixture-session"},"lease_current":True}
        n,s,tx=self.fixture("closed",prior=prior)
        self.assertEqual(self.claim(n,"fixture-key")["reason"],"claim_not_current")
        self.assertEqual(tx.run.call_count,2)
    def test_transaction_error_never_commits(self):
        n,s,tx=self.fixture()
        tx.run.side_effect=RuntimeError("fixture transaction error")
        with self.assertRaises(RuntimeError):self.claim(n)
        tx.commit.assert_not_called();s.run.assert_not_called()
    def test_invalid_setting_fails_closed(self):
        n,s,tx=self.fixture()
        with patch.dict("os.environ",{"ASSISTX_DIRECT_CLAIM_FENCE_REQUIRED":"typo"}):
            self.assertFalse(n.claim_task("fixture-task","fixture-agent")["claimed"])
        s.run.assert_not_called();tx.run.assert_not_called()

class MaintenanceTransitionTests(unittest.TestCase):
    def fixture(self, row, updated=None):
        from assistx.direct_claim_admission import set_direct_claim_admission
        s=MagicMock()
        tx=s.begin_transaction.return_value.__enter__.return_value
        tx.run.return_value.single.side_effect=[row,updated]
        return s,tx,set_direct_claim_admission
    def test_close_uses_same_lock_and_commits_new_revision(self):
        s,tx,change=self.fixture({"mode":"open","revision":4},{"mode":"closed","revision":5})
        self.assertEqual(change(s,mode="closed",expected_revision=4),{"changed":True,"mode":"closed","revision":5})
        self.assertIn("SET g.lock_sequence",tx.run.call_args_list[0].args[0])
        self.assertEqual(tx.run.call_args_list[1].args[1],{"mode":"closed","revision":5})
        tx.commit.assert_called_once()
    def test_stale_reopen_cannot_change_gate(self):
        s,tx,change=self.fixture({"mode":"closed","revision":5})
        self.assertFalse(change(s,mode="open",expected_revision=4)["changed"])
        self.assertEqual(tx.run.call_count,1)
        tx.commit.assert_not_called()
    def test_missing_singleton_does_not_create_gate(self):
        s,tx,change=self.fixture(None)
        self.assertFalse(change(s,mode="open",expected_revision=0)["changed"])
        self.assertEqual(tx.run.call_count,1)
        tx.commit.assert_not_called()
    def test_update_without_witness_rolls_back(self):
        s,tx,change=self.fixture({"mode":"open","revision":4},None)
        with self.assertRaises(RuntimeError):change(s,mode="closed",expected_revision=4)
        tx.commit.assert_not_called()
    def test_invalid_mode_and_revision_rejected(self):
        s,tx,change=self.fixture(None)
        for mode,rev in [("unknown",4),("open",True),("closed",-1)]:
            with self.assertRaises(ValueError):change(s,mode=mode,expected_revision=rev)
        s.begin_transaction.assert_not_called()
    def test_ambiguous_singleton_fails_closed(self):
        from neo4j.exceptions import ResultNotSingleError
        n,s,tx=DirectAdmissionTests().fixture()
        tx.run.return_value.single.side_effect=ResultNotSingleError("duplicate")
        tx.run.side_effect=None
        self.assertEqual(DirectAdmissionTests().claim(n)["reason"],"direct_claim_admission_unavailable")
        tx.commit.assert_not_called()
    def test_ambiguous_transition_never_updates(self):
        from neo4j.exceptions import ResultNotSingleError
        s,tx,change=self.fixture(None)
        tx.run.return_value.single.side_effect=ResultNotSingleError("duplicate")
        self.assertFalse(change(s,mode="closed",expected_revision=4)["changed"])
        self.assertEqual(tx.run.call_count,1)
        tx.commit.assert_not_called()
