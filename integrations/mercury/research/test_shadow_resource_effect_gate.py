"""Offline source-only negative controls for downstream shadow effect fencing."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

from shadow_resource_effect_gate import ResourceEffectGate, Replica, GROUP


class EffectGateTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="shadow-effect-")
        self.addCleanup(t.cleanup)
        self.path = str(Path(t.name) / "downstream.db")
        self.gate = ResourceEffectGate(self.path)
        self.gate.initialize_fixture("x1")
        self.x1 = Replica("x1", 1)

    def assert_nondispatch(self, value):
        self.assertIs(value["dispatch_authorized"], False)
        self.assertEqual(value["provider_calls"], 0)

    def test_reservation_only_no_real_effect(self):
        lease = self.gate.reserve(self.x1, "task-a", "nonce-a", 100)
        self.assertEqual(lease["decision"], "SHADOW_RESERVED_ONLY")
        self.assertIs(lease["shadow_effect_allowed"], False)
        self.assert_nondispatch(lease)
        effect = self.gate.check_effect(self.x1, "task-a", lease["shadow_token"], 101)
        self.assertEqual(effect["decision"], "SHADOW_EFFECT_ONLY")
        self.assert_nondispatch(effect)

    def test_copied_coordinator_state_cannot_bypass_resource(self):
        lease = self.gate.reserve(self.x1, "task-a", "nonce-a", 100)
        fake_copy = Replica("x1", 1)
        loser = self.gate.reserve(fake_copy, "task-b", "nonce-b", 101)
        self.assertEqual(loser["reason"], "slot_occupied")
        self.assertEqual(self.gate.check_effect(fake_copy, "task-b",
                                                lease["shadow_token"], 102)["decision"], "DENY")

    def test_partition_denies_already_reserved_effect(self):
        lease = self.gate.reserve(self.x1, "task-a", "nonce-a", 100)
        cut = Replica("x1", 1, connected=False)
        self.assertEqual(self.gate.reserve(cut, "task-b", "nonce-b", 110)["reason"],
                         "resource_unreachable")
        self.assertEqual(self.gate.check_effect(cut, "task-a", lease["shadow_token"],
                                                110)["reason"], "resource_unreachable")

    def test_revoke_while_old_token_live(self):
        lease = self.gate.reserve(self.x1, "task-a", "nonce-a", 100)
        new_epoch = self.gate.revoke_and_rotate_fixture("xwing")
        self.assertEqual(new_epoch, 2)
        self.assertFalse(self.gate.check_effect(self.x1, "task-a",
                                                lease["shadow_token"], 101)["shadow_effect_allowed"])
        self.assertEqual(self.gate.reserve(self.x1, "old", "nonce-b", 102)["reason"],
                         "stale_or_nonowner")
        new = self.gate.reserve(Replica("xwing", 2), "task-b", "nonce-c", 103)
        self.assertEqual(new["decision"], "SHADOW_RESERVED_ONLY")
        self.assertNotEqual(new["shadow_token"], lease["shadow_token"])

    def test_wrong_token_task_and_group(self):
        lease = self.gate.reserve(self.x1, "task-a", "nonce-a", 100)
        self.assertEqual(self.gate.check_effect(self.x1, "task-a", "fake", 101)["decision"], "DENY")
        self.assertEqual(self.gate.check_effect(self.x1, "task-b", lease["shadow_token"], 101)["decision"], "DENY")
        self.assertEqual(self.gate.check_effect(self.x1, "task-a", lease["shadow_token"],
                                                101, group="alias")["decision"], "DENY")

    def test_replay_and_monotonic_clock(self):
        self.gate.reserve(self.x1, "task-a", "nonce-a", 100)
        self.assertEqual(self.gate.reserve(self.x1, "task-a", "nonce-a", 101)["reason"],
                         "replayed_request")
        self.assertEqual(self.gate.reserve(self.x1, "task-b", "nonce-b", 99)["reason"],
                         "clock_rollback")

    def test_expired_effect_fails(self):
        lease = self.gate.reserve(self.x1, "task-a", "nonce-a", 100, ttl_ms=2)
        self.assertEqual(self.gate.check_effect(self.x1, "task-a",
                                                lease["shadow_token"], 102)["decision"], "DENY")

    def test_atomic_12_contenders(self):
        def reserve(i):
            return self.gate.reserve(self.x1, f"task-{i}", f"nonce-{i}", 100)
        with ThreadPoolExecutor(max_workers=8) as pool:
            all_results = list(pool.map(reserve, range(12)))
        self.assertEqual(sum(x["decision"] == "SHADOW_RESERVED_ONLY"
                             for x in all_results), 1)
        self.assertTrue(all(x["decision"] in ("SHADOW_RESERVED_ONLY", "DENY")
                            for x in all_results))
        self.assertTrue(all(x["provider_calls"] == 0 for x in all_results))

    def test_invalid_types_fail(self):
        self.assertEqual(self.gate.reserve(self.x1, "task", "nonce", True)["decision"], "DENY")
        self.assertEqual(self.gate.reserve(self.x1, "task", "nonce", -1)["decision"], "DENY")
        self.assertEqual(self.gate.reserve(self.x1, "task", "nonce", 100,
                                           ttl_ms=0)["decision"], "DENY")

    def test_deleted_resource_denies_effect(self):
        lease = self.gate.reserve(self.x1, "task-a", "nonce-a", 100)
        Path(self.path).unlink()
        self.assertEqual(self.gate.check_effect(self.x1, "task-a",
                                                lease["shadow_token"], 101)["decision"], "DENY")

    def test_copying_downstream_gate_recreates_split_brain(self):
        """NEGATIVE: even the downstream resource DB is NOT noncopyable."""
        target = str(Path(self.path).with_name("copy.db"))
        with sqlite3.connect(self.path) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
        clone = ResourceEffectGate(target)
        a = self.gate.reserve(self.x1, "task-a", "nonce-a", 100)
        b = clone.reserve(self.x1, "task-b", "nonce-b", 100)
        self.assertEqual(a["decision"], "SHADOW_RESERVED_ONLY")
        self.assertEqual(b["decision"], "SHADOW_RESERVED_ONLY")
        self.assert_nondispatch(a)
        self.assert_nondispatch(b)


if __name__ == "__main__":
    unittest.main()

