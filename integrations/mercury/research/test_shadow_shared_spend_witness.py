"""Provider-free denial/partition tests. NEVER imports production services."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from shadow_shared_spend_witness import ShadowClient, ShadowWitness, GROUP


class ShadowFenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mercury-witness-shadow-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.w = ShadowWitness(str(self.root / "witness.db"))
        self.w.bootstrap("x1")
        self.x1 = ShadowClient("x1", 1)

    def assert_no_execution(self, response):
        self.assertIs(response["dispatch_authorized"], False)
        self.assertEqual(response["provider_calls"], 0)

    def test_one_slot_two_copied_coordinators(self):
        a = self.w.acquire(self.x1, "a", 100)
        b = self.w.acquire(self.x1, "b", 100)
        self.assertEqual(a["decision"], "SHADOW_ADMITTED_ONLY")
        self.assertEqual(b["decision"], "occupied")
        self.assert_no_execution(a)
        self.assert_no_execution(b)

    def test_partition_denies_acquire_and_effect(self):
        lease = self.w.acquire(self.x1, "a", 100)["shadow_token"]
        cut = ShadowClient("x1", 1, connected=False)
        self.assertEqual(self.w.acquire(cut, "b", 110)["decision"],
                         "witness_unreachable")
        self.assertFalse(self.w.check_effect(cut, lease, 110)["shadow_effect_allowed"])

    def test_takeover_fences_stale_and_preserves_seat(self):
        token = self.w.acquire(self.x1, "a", 100)["shadow_token"]
        e = self.w.rotate("xwing")
        xwing = ShadowClient("xwing", e)
        self.assertFalse(self.w.check_effect(self.x1, token, 110)["shadow_effect_allowed"])
        self.assertEqual(self.w.acquire(xwing, "b", 110)["decision"], "occupied")
        self.assertEqual(self.w.acquire(xwing, "c", 1101)["decision"],
                         "SHADOW_ADMITTED_ONLY")

    def test_replay_and_clock_rollback(self):
        self.w.acquire(self.x1, "nonce", 100)
        self.assertEqual(self.w.acquire(self.x1, "nonce", 110)["decision"], "replay")
        self.assertEqual(self.w.acquire(self.x1, "new", 99)["decision"], "clock_rollback")

    def test_wrong_group_nonleader_and_stale_epoch(self):
        self.assertEqual(self.w.acquire(self.x1, "a", 100, group="alias")["decision"],
                         "invalid_request")
        self.assertEqual(self.w.acquire(ShadowClient("xwing", 1), "a", 100)["decision"],
                         "stale_or_nonleader")
        self.w.rotate("xwing")
        self.assertEqual(self.w.acquire(self.x1, "a", 100)["decision"],
                         "stale_or_nonleader")

    def test_concurrent_single_slot(self):
        def attempt(i):
            return self.w.acquire(self.x1, f"parallel-{i}", 100)
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(attempt, range(12)))
        self.assertEqual(sum(r["decision"] == "SHADOW_ADMITTED_ONLY" for r in rows), 1)
        self.assertTrue(all(r["decision"] in ("SHADOW_ADMITTED_ONLY", "occupied")
                            for r in rows))
        for row in rows:
            self.assert_no_execution(row)

    def test_full_witness_copy_still_splits_brain_negative(self):
        """Counterexample: duplicating the *witness* is still unsafe."""
        second_path = str(self.root / "copied-witness.db")
        with sqlite3.connect(self.w.path) as source, sqlite3.connect(second_path) as dst:
            source.backup(dst)
        copy = ShadowWitness(second_path)
        a = self.w.acquire(self.x1, "original", 100)
        b = copy.acquire(self.x1, "copied", 100)
        self.assertEqual(a["decision"], "SHADOW_ADMITTED_ONLY")
        self.assertEqual(b["decision"], "SHADOW_ADMITTED_ONLY")
        self.assert_no_execution(a)
        self.assert_no_execution(b)

    def test_bad_clock_types(self):
        self.assertEqual(self.w.acquire(self.x1, "a", True)["decision"], "invalid_request")
        self.assertEqual(self.w.acquire(self.x1, "a", -1)["decision"], "invalid_request")
        self.assert_no_execution(self.w.check_effect(self.x1, "fake", 100))


if __name__ == "__main__":
    unittest.main(verbosity=2)

