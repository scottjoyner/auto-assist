"""Offline Mercury shadow-ledger negative controls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from shadow_resource_effect_gate import ResourceEffectGate, Replica
from atomic_shadow_effect_ledger import AtomicShadowEffectLedger


class AtomicTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mercury-atomic-")
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "resource.db")
        self.gate = ResourceEffectGate(self.path)
        self.gate.initialize_fixture("x1")
        self.ledger = AtomicShadowEffectLedger(self.gate)
        self.client = Replica("x1", 1)
        lease = self.gate.reserve(self.client, "task-a", "nonce-a", 100)
        self.assertEqual(lease["decision"], "SHADOW_RESERVED_ONLY")
        self.token = lease["shadow_token"]

    def call(self, effect="effect-1", now=101, client=None, token=None):
        return self.ledger.record(client or self.client, "task-a",
                                  token or self.token, effect, now)

    def assert_inert(self, r):
        self.assertIs(r["dispatch_authorized"], False)
        self.assertEqual(r["provider_calls"], 0)
        self.assertEqual(r["real_effects"], 0)

    def test_atomic_record(self):
        r = self.call()
        self.assertEqual(r["decision"], "SHADOW_RECORDED_ONLY")
        self.assert_inert(r)
        self.assertEqual(self.ledger.local_check()["count"], 1)

    def test_lost_ack_quarantines_replay(self):
        self.call()
        r = self.call(now=102)
        self.assertEqual(r["reason"], "ambiguous_prior_ack_no_replay")
        self.assertEqual(self.ledger.local_check()["count"], 1)
        self.assert_inert(r)

    def test_prior_positive_check_does_not_survive_revoke(self):
        self.assertTrue(self.gate.check_effect(self.client, "task-a",
                                               self.token, 101)["shadow_effect_allowed"])
        self.gate.revoke_and_rotate_fixture("xwing")
        self.assertEqual(self.call()["decision"], "DENY")
        self.assertEqual(self.ledger.local_check()["count"], 0)

    def test_partition_and_wrong_token(self):
        cut = Replica("x1", 1, False)
        self.assertEqual(self.call(client=cut)["reason"], "resource_unreachable")
        self.assertEqual(self.call(token="0"*32)["decision"], "DENY")

    def test_expired_and_invalid_time(self):
        self.assertEqual(self.call(now=1100)["decision"], "DENY")
        self.assertEqual(self.call(now=99)["decision"], "DENY")
        self.assertEqual(self.call(now=True)["decision"], "DENY")

    def test_duplicate_concurrent_exactly_once(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.call(), range(12)))
        self.assertEqual(sum(x["decision"] == "SHADOW_RECORDED_ONLY" for x in results), 1)
        self.assertTrue(all(x["decision"] in ("SHADOW_RECORDED_ONLY", "QUARANTINE")
                            for x in results))
        self.assertEqual(self.ledger.local_check()["count"], 1)

    def test_revoke_after_commit_preserves_row_not_authority(self):
        self.call()
        self.gate.revoke_and_rotate_fixture("xwing")
        self.assertEqual(self.call(effect="effect-2")["decision"], "DENY")
        self.assertEqual(self.ledger.local_check()["count"], 1)

    def test_tamper_detected_locally(self):
        self.call()
        with sqlite3.connect(self.path) as con:
            con.execute("UPDATE shadow_effect_ledger SET attempt='evil'")
        self.assertFalse(self.ledger.local_check()["local_chain_ok"])

    def test_deleted_history_invisible_without_external_witness(self):
        self.call()
        with sqlite3.connect(self.path) as con:
            con.execute("DELETE FROM shadow_effect_ledger")
        r = self.ledger.local_check()
        self.assertTrue(r["local_chain_ok"])
        self.assertEqual(r["count"], 0)
        self.assertFalse(r["independent_custody_verified"])

    def test_resource_copy_still_splits_brain(self):
        second = str(Path(self.path).with_name("copy.db"))
        with sqlite3.connect(self.path) as src, sqlite3.connect(second) as dst:
            src.backup(dst)
        other = AtomicShadowEffectLedger(ResourceEffectGate(second))
        a = self.call("effect-a")
        b = other.record(self.client, "task-a", self.token, "effect-b", 101)
        self.assertEqual(a["decision"], "SHADOW_RECORDED_ONLY")
        self.assertEqual(b["decision"], "SHADOW_RECORDED_ONLY")
        self.assert_inert(a)
        self.assert_inert(b)


if __name__ == "__main__":
    unittest.main()

