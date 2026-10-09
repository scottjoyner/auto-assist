"""Hermetic single-batch, no-NAS validation for legacy ACK hash witness."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bounded_ack_hash_witness as w


class HashBatchTests(unittest.TestCase):
    def setUp(self):
        self.rows=[{"name":f"trc-x1-370-20261009T{i:06d}Z-1234abcd.tar.zst",
                    "size":100+i,"sha":f"{i:064x}"}
                   for i in range(40)]

    def test_eight_bounded_spread_entries(self):
        chosen=w.select_batch(self.rows,{})
        self.assertEqual(len(chosen),8)
        self.assertLessEqual(sum(x["size"] for x in chosen),w.MAX_BYTES)
        self.assertEqual(len({x["name"] for x in chosen}),8)

    def test_resume_skips_exact_prior_witnesses(self):
        first=w.select_batch(self.rows,{})
        prior={w.sha(x["name"].encode()):(x["sha"],x["size"]) for x in first}
        later=w.select_batch(self.rows,prior)
        self.assertEqual(len(later),8)
        self.assertTrue({x["name"] for x in first}.isdisjoint(
                        {x["name"] for x in later}))

    def test_changed_prior_ack_fails_closed(self):
        x=self.rows[0]
        prior={w.sha(x["name"].encode()):("f"*64,x["size"])}
        with self.assertRaisesRegex(RuntimeError,"ACK mutated"):
            w.select_batch(self.rows,prior)

    def test_budget_never_exceeded(self):
        rows=[dict(x,size=w.MAX_BYTES-100) for x in self.rows[:8]]
        chosen=w.select_batch(rows,{})
        self.assertEqual(len(chosen),1)

    def test_journal_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"private.jsonl"
            fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600)
            try:
                selected=self.rows[:3]
                w.record_batch(fd,selected)
                old=w.verified_prior(fd)
                self.assertEqual(len(old),3)
                self.assertEqual(old[w.sha(selected[0]["name"].encode())],
                                 (selected[0]["sha"],selected[0]["size"]))
            finally:os.close(fd)

    def test_invalid_journal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"private.jsonl"
            path.write_text('{"schema":"not-a-proof","entries":[]}\n')
            fd=os.open(path,os.O_RDONLY)
            try:
                with self.assertRaisesRegex(RuntimeError,"untrusted private journal"):
                    w.verified_prior(fd)
            finally:os.close(fd)

    def test_stricter_research_headroom(self):
        self.assertTrue(w.research_headroom_ok({"util_pct":"44.5","iowait_pct":"8.91"}))
        self.assertFalse(w.research_headroom_ok({"util_pct":"15.7","iowait_pct":"26.84"}))
        self.assertFalse(w.research_headroom_ok({"util_pct":"70","iowait_pct":"1"}))
        self.assertFalse(w.research_headroom_ok({"util_pct":"unknown","iowait_pct":"1"}))

    def test_official_go_still_holds_at_high_iowait(self):
        with patch.object(w.subprocess,"run") as call:
            call.side_effect=[
                type("Result",(),{"returncode":0,"stdout":"/dev/nvme1n1p1 ext4"})(),
                type("Result",(),{"returncode":0,"stdout":
                     "\n".join(("lane=L1","decision=GO","btrfs_errors=0","util_pct=15.7","iowait_pct=26.84"))})()]
            with self.assertRaisesRegex(RuntimeError,"RESEARCH_HEADROOM_HOLD"):
                w.preflight_go()

    def test_no_go_always_holds_even_with_headroom(self):
        with patch.object(w.subprocess,"run") as call:
            call.side_effect=[
                type("Result",(),{"returncode":0,"stdout":"/dev/nvme1n1p1 ext4"})(),
                type("Result",(),{"returncode":10,"stdout":"lane=L1\ndecision=THROTTLE\nbtrfs_errors=0\nutil_pct=2\niowait_pct=1"})()]
            with self.assertRaisesRegex(RuntimeError,"NO_L1_GO"):
                w.preflight_go()


if __name__=="__main__":
    unittest.main(verbosity=2)

