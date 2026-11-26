"""Failure reports must not overwrite another execution claim."""
import unittest
from unittest.mock import MagicMock
from assistx.swarm_core import fail_task

class FailureFenceTests(unittest.TestCase):
    def fixture(self,task):
        neo=MagicMock()
        session=neo._session.return_value.__enter__.return_value
        tx=session.begin_transaction.return_value.__enter__.return_value
        tx.run.return_value.single.return_value={"t":task} if task else None
        return neo,session,tx
    def test_claim_identity_and_lease_required_in_transaction(self):
        n,s,tx=self.fixture({"status":"READY"})
        result=fail_task(n,"t","a","fixture",session_id="s",claim_id="c")
        self.assertEqual(result,{"status":"READY"})
        query,params=tx.run.call_args.args
        for expr in ("SET t.failure_fence_sequence","t.claimed_by=$agent_id","t.claim_id=$claim_id","t.lease_expires_at_ts > timestamp()","t.agent_session_id","t.status IN ['CLAIMED','RUNNING']"):
            self.assertIn(expr,query)
        self.assertEqual(params["claim_id"],"c")
        self.assertEqual(params["session_id"],"s")
        tx.commit.assert_called_once()
        s.run.assert_not_called()
    def test_missing_or_stale_owner_rolls_back(self):
        n,s,tx=self.fixture(None)
        self.assertIsNone(fail_task(n,"t","a","fixture",claim_id="stale"))
        tx.commit.assert_not_called()
        s.run.assert_not_called()
    def test_legacy_path_requires_unowned_ready_task(self):
        n,s,tx=self.fixture({"status":"READY"})
        fail_task(n,"t","a","fixture")
        query,params=tx.run.call_args.args
        self.assertIn("t.status='READY'",query)
        self.assertIn("t.claim_id IS NULL",query)
        self.assertIn("t.claimed_by IS NULL",query)
        self.assertIn("t.passive_claim_id IS NULL",query)
        self.assertIsNone(params["claim_id"])
    def test_successful_failure_clears_current_lease_fields(self):
        n,s,tx=self.fixture({"status":"FAILED"})
        fail_task(n,"t","a","fixture",retryable=False,claim_id="c")
        query,params=tx.run.call_args.args
        for expr in ("t.claimed_by=null","t.claim_id=null","t.agent_session_id=null","t.lease_expires_at_ts=null","t.last_failed_claim_id=$claim_id"):
            self.assertIn(expr,query)
        self.assertEqual(params["status"],"FAILED")
    def test_transaction_exception_does_not_commit(self):
        n,s,tx=self.fixture(None)
        tx.run.side_effect=RuntimeError("fixture")
        with self.assertRaises(RuntimeError):
            fail_task(n,"t","a","fixture",claim_id="c")
        tx.commit.assert_not_called()
