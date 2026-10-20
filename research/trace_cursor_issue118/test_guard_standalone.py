"""Dependency-free, research-only synthetic checks for the issue #118 guard and seal index."""
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from cursor_window_guard import SourceWindowHold, capture_window
from seal_index import build_seal_index, check_seal_index


class SourceWindowGuardTests(unittest.TestCase):
    def setUp(self):
        self.owner=tempfile.TemporaryDirectory(prefix="issue118-guard-fixture-")
        self.addCleanup(self.owner.cleanup)
        self.root=pathlib.Path(self.owner.name)
        self.path=self.root/"capture.jsonl"

    def test_torn_tail_does_not_commit(self):
        first=b'{"n":1}\n'
        self.path.write_bytes(first+b'{"n":2')
        data,state,receipt=capture_window(self.path,None)
        self.assertEqual(data,first)
        self.assertEqual(state["offset"],len(first))
        self.assertEqual(receipt["offset_end_committed"],len(first))
        self.assertTrue(receipt["torn_tail_dropped"])

    def test_completed_tail_can_resume(self):
        first=b'{"n":1}\n'
        second=b'{"n":2}\n'
        self.path.write_bytes(first+second[:-1])
        one,state,_=capture_window(self.path,None)
        self.path.write_bytes(first+second)
        two,next_state,receipt=capture_window(self.path,state)
        self.assertEqual(one,first)
        self.assertEqual(two,second)
        self.assertEqual(next_state["offset"],len(first+second))
        self.assertEqual(receipt["offset_from"],len(first))

    def test_duplicate_retry_emits_zero(self):
        data=b'{"done":true}\n'
        self.path.write_bytes(data)
        _,state,_=capture_window(self.path,None)
        payload,next_state,_=capture_window(self.path,state)
        self.assertEqual(payload,b"")
        self.assertEqual(next_state["offset"],len(data))

    def test_same_size_overwrite_denied(self):
        self.path.write_bytes(b'{"i":1}\n')
        _,state,_=capture_window(self.path,None)
        self.path.write_bytes(b'{"i":2}\n')
        with self.assertRaises(SourceWindowHold):
            capture_window(self.path,state)

    def test_false_inode_denied(self):
        self.path.write_bytes(b'{"i":1}\n')
        _,state,_=capture_window(self.path,None)
        state["source_inode"]+=1
        with self.assertRaises(SourceWindowHold):
            capture_window(self.path,state)

    def test_legacy_checkpoint_denied(self):
        self.path.write_bytes(b'{"i":1}\n')
        with self.assertRaises(SourceWindowHold):
            capture_window(self.path,{"offset":8})

    def test_forged_mid_record_boundary_denied(self):
        self.path.write_bytes(b'{"i":1}\n')
        _,state,_=capture_window(self.path,None)
        state["offset"]=3
        state["prefix_sha256"]=hashlib.sha256(self.path.read_bytes()[:3]).hexdigest()
        with self.assertRaises(SourceWindowHold):
            capture_window(self.path,state)

    def test_symlink_denied(self):
        destination=self.root/"real.jsonl"
        destination.write_bytes(b'{"i":1}\n')
        self.path.symlink_to(destination)
        with self.assertRaises(OSError):
            capture_window(self.path,None)


class SealIndexTests(unittest.TestCase):
    def setUp(self):
        self.owner=tempfile.TemporaryDirectory(prefix="issue118-index-fixture-")
        self.addCleanup(self.owner.cleanup)
        self.root=pathlib.Path(self.owner.name)
        self.source=self.root/"source.jsonl"
        self.stem="fixture-01234567.tar.zst"

    def stage(self, *, ready=True, manifest=True, committed_end=10,
              wrong_ready=False, rows=True):
        (self.root/self.stem).write_bytes(b"synthetic-tar-reference-not-actual-tar")
        digest=hashlib.sha256(b"synthetic-tar-reference-not-actual-tar").hexdigest()
        if manifest:
            payload={"archive":self.stem,"archive_sha256":digest,
                "files":[{"source_group":"dc_traces","source":str(self.source),
                         **({"offset_end_committed":committed_end} if rows else {})}]}
            (self.root/(self.stem+".manifest.json")).write_text(json.dumps(payload))
        if ready:
            (self.root/(self.stem+".ready")).write_text(
                ("0"*64 if wrong_ready else digest)+"\n")

    def test_complete_triple_expected_source(self):
        self.stage()
        index=build_seal_index(self.root)
        self.assertFalse(index["errors"])
        self.assertEqual(index["archives"],1)
        self.assertEqual(check_seal_index(index,self.source,"dc_traces",10),[])

    def test_checkpoint_behind_seal_denied(self):
        self.stage()
        index=build_seal_index(self.root)
        self.assertTrue(check_seal_index(index,self.source,"dc_traces",9))

    def test_orphan_archive_denied(self):
        self.stage(manifest=False,ready=False)
        index=build_seal_index(self.root)
        self.assertTrue(index["errors"])
        self.assertTrue(check_seal_index(index,self.source,"dc_traces",0))

    def test_missing_ready_denied(self):
        self.stage(ready=False)
        self.assertTrue(build_seal_index(self.root)["errors"])

    def test_wrong_ready_digest_denied(self):
        self.stage(wrong_ready=True)
        self.assertTrue(build_seal_index(self.root)["errors"])

    def test_legacy_unknown_committed_end_denied(self):
        self.stage(rows=False)
        index=build_seal_index(self.root)
        self.assertFalse(index["errors"])
        self.assertTrue(check_seal_index(index,self.source,"dc_traces",100))

    def test_other_source_does_not_claim_our_archive(self):
        self.stage()
        index=build_seal_index(self.root)
        self.assertEqual(check_seal_index(index,self.root/"other.jsonl","dc_traces",0),[])


if __name__=="__main__":
    unittest.main(verbosity=2)