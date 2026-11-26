"""Sweep cannot advance the server clock or release a newly renewed lease."""
import unittest
from unittest.mock import MagicMock
from assistx.swarm_core import release_expired_task_leases

class ExpiryFenceTests(unittest.TestCase):
    def fixture(self, row):
        neo=MagicMock()
        session=neo._session.return_value.__enter__.return_value
        session.run.return_value.single.return_value=row
        return neo,session
    def test_server_time_caps_requested_cutoff(self):
        n,s=self.fixture({"released":0})
        self.assertEqual(release_expired_task_leases(n,now_ms=10**15),0)
        query,params=s.run.call_args.args
        self.assertIn("CASE WHEN $now IS NULL OR $now > timestamp() THEN timestamp() ELSE $now END",query)
        self.assertEqual(params["now"],10**15)
    def test_rechecks_status_and_expiry_after_lock(self):
        n,s=self.fixture({"released":1})
        self.assertEqual(release_expired_task_leases(n,now_ms=1),1)
        q=s.run.call_args.args[0]
        lock=q.index("SET t.failure_fence_sequence")
        release=q.index("SET t.status='READY'")
        self.assertIn("t.status IN ['CLAIMED','RUNNING']",q[lock:release])
        self.assertIn("t.lease_expires_at_ts < cutoff",q[lock:release])
        self.assertIn("t.passive_claim_id IS NULL",q[lock:release])
        self.assertIn("LIMIT 2000",q[:lock])
    def test_missing_lease_is_not_expiry_evidence(self):
        n,s=self.fixture({"released":0})
        release_expired_task_leases(n)
        q,p=s.run.call_args.args
        self.assertNotIn("coalesce(t.lease_expires_at_ts, 0)",q)
        self.assertIsNone(p["now"])
    def test_clears_lease_metadata_on_release(self):
        n,s=self.fixture({"released":2})
        self.assertEqual(release_expired_task_leases(n),2)
        q=s.run.call_args.args[0]
        for field in ("claimed_by","agent_session_id","claim_id","lease_expires_at_ts","claimed_at_ts","last_heartbeat_at_ts","last_heartbeat_ts"):
            self.assertIn("t."+field+"=null",q)
    def test_invalid_cutoff_rejected_without_graph(self):
        for value in (-1,True,1.25,"1"):
            n,s=self.fixture(None)
            with self.assertRaises(ValueError):
                release_expired_task_leases(n,now_ms=value)
            n._session.assert_not_called()
