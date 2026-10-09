#!/usr/bin/env python3
"""Safe trace drainer contract: receipts, fail-closed admission, no deletions."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import trace_drainer_safe as tr

class SafeTraceDrainerTests(unittest.TestCase):
    def make_source(self,root):
        sealed=root/"sealed"
        sealed.mkdir(parents=True,exist_ok=True)
        name="trc-x1-370-20261008T150000Z-1234abcd.tar.zst"
        path=sealed/name
        path.write_bytes(b"sealed archive bytewise custody fixture")
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        (sealed/(name+".ready")).write_text(sha+"\n")
        (sealed/(name+".manifest.json")).write_text('{"schema": 1}\n')
        return path,sha

    def fake_transfer(self,args,timeout=30):
        op=args[0]
        if op=="rsync":
            shutil.copy2(args[-2],args[-1])
        elif op=="mv":
            src=Path(args[-2])
            dest=Path(args[-1])
            if not dest.exists():
                shutil.move(src,dest)
        else:
            raise AssertionError("unexpected command "+op)
        return subprocess.CompletedProcess(args,0,"","")

    def test_sha_copy_and_ack_preserve_original_and_receipts(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            src,sha=self.make_source(root)
            remote=root/"nas-inbox"
            remote.mkdir()
            with patch.object(tr,"INBOX",remote),patch.object(tr,"call",self.fake_transfer):
                report=tr.publish_one(src,sha,src.stat().st_size)
                self.assertEqual(report["state"],"published_verified")
                self.assertEqual((remote/src.name).read_bytes(),src.read_bytes())
                self.assertEqual((remote/(src.name+".ready")).read_bytes(),
                                 src.with_name(src.name+".ready").read_bytes())
                self.assertTrue(src.exists())
                self.assertTrue(src.with_name(src.name+".ready").exists())
                self.assertTrue(src.with_name(src.name+".manifest.json").exists())
                ack=json.loads(src.with_name(src.name+".ack.json").read_text())
                self.assertEqual(ack["source_release"],"BLOCKED")
                self.assertEqual(ack["sha256"],sha)
                ready_sha=hashlib.sha256(src.with_name(src.name+".ready").read_bytes()).hexdigest()
                self.assertEqual(ack["ready_sha256"],ready_sha)
                self.assertEqual(tr.eligible(src.parent),[])

    def test_conflicting_nas_copy_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            src,sha=self.make_source(root)
            remote=root/"nas-inbox"
            remote.mkdir()
            existing=remote/src.name
            existing.write_bytes(b"different bytes: protected evidence")
            with patch.object(tr,"INBOX",remote):
                with self.assertRaisesRegex(RuntimeError,"CUSTODY CONFLICT"):
                    tr.publish_one(src,sha,src.stat().st_size)
            self.assertEqual(existing.read_bytes(),b"different bytes: protected evidence")
            self.assertTrue(src.exists())
            self.assertFalse(src.with_name(src.name+".ack.json").exists())

    def test_existing_nas_without_ready_cannot_be_acknowledged(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            src,sha=self.make_source(root)
            remote=root/"nas-inbox"
            remote.mkdir()
            (remote/src.name).write_bytes(src.read_bytes())
            (remote/(src.name+".manifest.json")).write_bytes(
                src.with_name(src.name+".manifest.json").read_bytes())
            with patch.object(tr,"INBOX",remote):
                with self.assertRaisesRegex(RuntimeError,"CUSTODY CONFLICT"):
                    tr.publish_one(src,sha,src.stat().st_size)
            self.assertFalse(src.with_name(src.name+".ack.json").exists())
            self.assertFalse((remote/(src.name+".ready")).exists())

    def test_remote_ready_digest_mismatch_blocks_ack(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            src,sha=self.make_source(root)
            remote=root/"nas-inbox"
            remote.mkdir()
            (remote/src.name).write_bytes(src.read_bytes())
            (remote/(src.name+".manifest.json")).write_bytes(
                src.with_name(src.name+".manifest.json").read_bytes())
            (remote/(src.name+".ready")).write_text("0"*64+"\\n")
            with patch.object(tr,"INBOX",remote):
                with self.assertRaisesRegex(RuntimeError,"CUSTODY CONFLICT"):
                    tr.publish_one(src,sha,src.stat().st_size)
            self.assertFalse(src.with_name(src.name+".ack.json").exists())

    def test_ready_upload_failure_never_emits_ack(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            src,sha=self.make_source(root)
            remote=root/"nas-inbox"
            remote.mkdir()
            def transfer_fail_ready(args,timeout=30):
                if args[0]=="rsync" and str(args[-2]).endswith(".ready"):
                    return subprocess.CompletedProcess(args,2,"","injected failure")
                return self.fake_transfer(args,timeout)
            with patch.object(tr,"INBOX",remote),patch.object(tr,"call",transfer_fail_ready):
                with self.assertRaisesRegex(RuntimeError,"ready staging failed"):
                    tr.publish_one(src,sha,src.stat().st_size)
            self.assertTrue(src.exists())
            self.assertTrue(src.with_name(src.name+".ready").exists())
            self.assertTrue((remote/src.name).is_file())
            self.assertTrue((remote/(src.name+".manifest.json")).is_file())
            self.assertFalse((remote/(src.name+".ready")).exists())
            self.assertFalse(src.with_name(src.name+".ack.json").exists())

    def test_existing_complete_nas_triplet_can_be_acknowledged(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            src,sha=self.make_source(root)
            remote=root/"nas-inbox"
            remote.mkdir()
            for suffix in ("",".manifest.json",".ready"):
                local=src.with_name(src.name+suffix)
                (remote/(src.name+suffix)).write_bytes(local.read_bytes())
            with patch.object(tr,"INBOX",remote):
                report=tr.publish_one(src,sha,src.stat().st_size)
            self.assertEqual(report["state"],"already_verified")
            self.assertTrue(src.with_name(src.name+".ack.json").exists())

    def test_legacy_ack_without_ready_proof_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            src,sha=self.make_source(root)
            manifest=src.with_name(src.name+".manifest.json")
            ack={"schema":"safe-trace-nas-ack/v1","sha256":sha,
                 "bytes":src.stat().st_size,"manifest_sha256":tr.digest(manifest)}
            src.with_name(src.name+".ack.json").write_text(json.dumps(ack))
            with self.assertRaisesRegex(RuntimeError,"legacy acknowledgement lacks ready"):
                tr.eligible(src.parent)
            self.assertTrue(src.exists())
            self.assertTrue(src.with_name(src.name+".ack.json").exists())

    def test_existing_remote_ready_collision_preserves_both_copies(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            src,sha=self.make_source(root)
            remote=root/"nas-inbox"
            remote.mkdir()
            collision=remote/(src.name+".ready")
            collision.write_text("0"*64+"\\n")
            with patch.object(tr,"INBOX",remote),patch.object(tr,"call",self.fake_transfer):
                with self.assertRaisesRegex(RuntimeError,"ready publish collision"):
                    tr.publish_one(src,sha,src.stat().st_size)
            self.assertTrue(collision.exists())
            self.assertFalse(src.with_name(src.name+".ack.json").exists())
            self.assertTrue(src.exists())

    def test_local_ready_missing_never_publishes(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            src,sha=self.make_source(root)
            src.with_name(src.name+".ready").unlink()
            remote=root/"nas-inbox"
            remote.mkdir()
            with patch.object(tr,"INBOX",remote),patch.object(tr,"call",self.fake_transfer):
                with self.assertRaisesRegex(RuntimeError,"source ready marker missing"):
                    tr.publish_one(src,sha,src.stat().st_size)
            self.assertFalse(list(remote.iterdir()))
            self.assertFalse(src.with_name(src.name+".ack.json").exists())

    def test_status_history_grows_without_trim(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            (root/"state").mkdir()
            tr.status_write(root,{"verdict":"held_admission"})
            tr.status_write(root,{"verdict":"verified"})
            lines=(root/"state/safe-drain-history.jsonl").read_text().splitlines()
            self.assertEqual(len(lines),2)
            self.assertEqual(json.loads(lines[0])["verdict"],"held_admission")
            self.assertEqual(json.loads(lines[1])["verdict"],"verified")

    def test_holding_nas_never_copies(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            self.make_source(root)
            (root/"state").mkdir()
            with patch.object(tr,"SPOOL",root),patch.object(tr,"source_preflight"), \
                 patch.object(tr,"admission",return_value="HOLD"), \
                 patch.object(tr,"nas_preflight",side_effect=AssertionError("must not access NAS")):
                report=tr.execute()
            self.assertEqual(report["verdict"],"held_admission")
            self.assertEqual(report["local_bytes_reclaimed"],0)
            self.assertTrue(list((root/"sealed").glob("trc-*.tar.zst")))

if __name__=="__main__":
    unittest.main(verbosity=2)
