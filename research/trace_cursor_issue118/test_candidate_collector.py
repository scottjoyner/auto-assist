"""Actual capture() hermetic evidence for an uninstalled fail-closed candidate."""
import hashlib
import json
import pathlib
import sys
import unittest
import sandbox_fixture as baseline

CANDIDATE = pathlib.Path(__file__).with_name("trace-spool-capture.py")
BASELINE = pathlib.Path(__file__).with_name("baseline-trace-spool-capture.py")
ORIGINAL_SHA = "fba0d51b9a73c9de5f4a69705b2c325b18417cd8e051400e8c30ec31f3bcdbfe"
baseline.COLLECTOR = CANDIDATE


class CandidateCollectorTests(unittest.TestCase):
    def setUp(self):
        self.fx = baseline.CollectorSandboxTests(
            "test_no_new_append_emits_no_duplicate_seal")
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.m = self.fx.module(False)

    def captured(self):
        return b"".join(self.fx.archive_text(p) for p in self.fx.manifests())

    def state(self):
        path=self.fx.spool/"state"/"offsets.json"
        return json.loads(path.read_text()) if path.exists() else None

    def test_complete_line_append_then_resume(self):
        a,b=self.fx.fixture_line("FIRST"),self.fx.fixture_line("SECOND")
        self.fx.trace.write_bytes(a+b[:5])
        self.assertEqual(self.m.capture(self.fx.args),0)
        self.assertEqual(self.fx.source_cursor(),len(a))
        m=self.fx.manifests()[0]
        row=json.loads(m.read_text())["files"][0]
        self.assertEqual(row["size_at_open"],len(a)+5)
        self.assertEqual(row["offset_end_committed"],len(a))
        self.assertIn("prefix_sha256",row)
        self.fx.trace.write_bytes(a+b)
        self.assertEqual(self.fx.module(False).capture(self.fx.args),0)
        self.assertEqual(self.captured(),a+b)
        self.assertEqual(len(self.fx.manifests()),2)

    def test_partial_no_newline_does_not_commit(self):
        a=self.fx.fixture_line("ZERO")
        self.fx.trace.write_bytes(a[:-1])
        self.assertEqual(self.m.capture(self.fx.args),0)
        self.assertIsNone(self.state())
        self.assertEqual(self.fx.manifests(),[])
        self.fx.trace.write_bytes(a)
        self.assertEqual(self.m.capture(self.fx.args),0)
        self.assertEqual(self.captured(),a)

    def test_same_size_inplace_replacement_hold(self):
        a,b=self.fx.fixture_line("AA"),self.fx.fixture_line("BB")
        self.assertEqual(len(a),len(b))
        self.fx.trace.write_bytes(a)
        self.assertEqual(self.m.capture(self.fx.args),0)
        self.fx.trace.write_bytes(b)
        self.assertEqual(self.m.capture(self.fx.args),1)
        self.assertEqual(len(self.fx.manifests()),1)
        self.assertEqual(self.captured(),a)
        self.assertEqual(self.fx.source_cursor(),len(a))

    def test_inode_rotation_hold(self):
        a,b=self.fx.fixture_line("ONE"),self.fx.fixture_line("TWO")
        self.fx.trace.write_bytes(a)
        self.assertEqual(self.m.capture(self.fx.args),0)
        old=self.fx.trace.with_suffix(".previous")
        self.fx.trace.rename(old)
        self.fx.trace.write_bytes(b)
        self.assertEqual(self.m.capture(self.fx.args),1)
        self.assertEqual(self.fx.source_cursor(),len(a))
        self.assertEqual(len(self.fx.manifests()),1)

    def test_truncation_fails_closed(self):
        a=self.fx.fixture_line("THIS_IS_LONG_MARKER")
        b=self.fx.fixture_line("X")
        self.fx.trace.write_bytes(a)
        self.assertEqual(self.m.capture(self.fx.args),0)
        self.fx.trace.write_bytes(b)
        self.assertEqual(self.m.capture(self.fx.args),1)
        self.assertEqual(self.captured(),a)

    def test_legacy_cursor_requires_migration(self):
        a=self.fx.fixture_line("FIRST")
        self.fx.trace.write_bytes(a)
        self.assertEqual(self.m.capture(self.fx.args),0)
        path=self.fx.spool/"state"/"offsets.json"
        state=self.state()
        k="dc_traces:"+str(self.fx.trace)
        state["sources"][k]={"offset":len(a),"updated":"legacy"}
        path.write_text(json.dumps(state))
        self.fx.trace.write_bytes(a+self.fx.fixture_line("NEXT"))
        self.assertEqual(self.m.capture(self.fx.args),1)
        self.assertEqual(len(self.fx.manifests()),1)

    def test_crash_after_seal_does_not_duplicate(self):
        a=self.fx.fixture_line("CRASH")
        self.fx.trace.write_bytes(a)
        orig=self.m.Spool.save_offsets
        def fail_checkpoint(_self,_state):
            raise OSError("injected fault before offsets persistence")
        self.m.Spool.save_offsets=fail_checkpoint
        with self.assertRaisesRegex(OSError,"injected fault"):
            self.m.capture(self.fx.args)
        self.assertEqual(len(self.fx.manifests()),1)
        self.assertIsNone(self.state())
        # Restart of isolated source, old manifest/ready exists.
        self.assertEqual(self.fx.module(False).capture(self.fx.args),1)
        self.assertEqual(len(self.fx.manifests()),1)
        self.assertEqual(self.captured(),a)
        self.m.Spool.save_offsets=orig

    def test_repeat_no_duplicate(self):
        a=self.fx.fixture_line("ONCE")
        self.fx.trace.write_bytes(a)
        self.assertEqual(self.m.capture(self.fx.args),0)
        self.assertEqual(self.m.capture(self.fx.args),0)
        self.assertEqual(len(self.fx.manifests()),1)

    def test_existing_source_files_remain_identical(self):
        self.assertEqual(hashlib.sha256(BASELINE.read_bytes()).hexdigest(),ORIGINAL_SHA)

    def test_orphan_archive_without_manifest_holds(self):
        a=self.fx.fixture_line("ORPHAN")
        self.fx.trace.write_bytes(a)
        (self.fx.spool/"sealed").mkdir(parents=True,exist_ok=True)
        (self.fx.spool/"sealed"/"fixture-orphan.tar.zst").write_bytes(b"synthetic")
        self.assertEqual(self.m.capture(self.fx.args),1)
        self.assertIsNone(self.state())
        self.assertEqual(len(self.fx.manifests()),0)

    def test_missing_ready_marker_holds_before_next_capture(self):
        a=self.fx.fixture_line("FIRST")
        self.fx.trace.write_bytes(a)
        self.assertEqual(self.m.capture(self.fx.args),0)
        archive=self.fx.manifests()[0]
        pathlib.Path(str(archive).removesuffix(".manifest.json")+".ready").unlink()
        self.fx.trace.write_bytes(a+self.fx.fixture_line("NEXT"))
        self.assertEqual(self.m.capture(self.fx.args),1)
        self.assertEqual(len(self.fx.manifests()),1)

    def test_tampered_ready_marker_holds(self):
        a=self.fx.fixture_line("FIRST")
        self.fx.trace.write_bytes(a)
        self.assertEqual(self.m.capture(self.fx.args),0)
        archive=self.fx.manifests()[0]
        pathlib.Path(str(archive).removesuffix(".manifest.json")+".ready").write_text("bad")
        self.fx.trace.write_bytes(a+self.fx.fixture_line("SECOND"))
        self.assertEqual(self.m.capture(self.fx.args),1)
        self.assertEqual(len(self.fx.manifests()),1)

    def test_corrupt_state_does_not_reset_to_zero(self):
        a=self.fx.fixture_line("FIRST")
        self.fx.trace.write_bytes(a)
        self.assertEqual(self.m.capture(self.fx.args),0)
        (self.fx.spool/"state"/"offsets.json").write_text("{not json")
        self.fx.trace.write_bytes(a+self.fx.fixture_line("SECOND"))
        with self.assertRaises(self.m.SourceWindowHold):
            self.m.capture(self.fx.args)
        self.assertEqual(len(self.fx.manifests()),1)

    def test_two_source_groups_share_one_index_scan(self):
        hosted=self.fx.fixture_line("HOSTED")
        isolated=self.fx.fixture_line("ISOLATED")
        isolated_path=self.fx.dc_isolated/"tool-history.jsonl"
        self.fx.trace.write_bytes(hosted)
        isolated_path.write_bytes(isolated)
        original=self.m.build_seal_index
        seen=[]
        def count_scan(path):
            seen.append(path)
            return original(path)
        self.m.build_seal_index=count_scan
        self.assertEqual(self.m.capture(self.fx.args),0)
        self.assertEqual(len(seen),1)
        self.assertEqual(len(self.fx.manifests()),1)
        manifest=json.loads(self.fx.manifests()[0].read_text())
        groups={f["source_group"] for f in manifest["files"]}
        self.assertEqual(groups,{"dc_traces","dc_traces_selfhosted"})

    def test_manifest_without_archive_is_custody_hold(self):
        self.fx.trace.write_bytes(self.fx.fixture_line("ONE"))
        sealed=self.fx.spool/"sealed"
        sealed.mkdir(parents=True,exist_ok=True)
        (sealed/"orphan.tar.zst.manifest.json").write_text("{}")
        self.assertEqual(self.m.capture(self.fx.args),1)
        self.assertEqual(len(self.fx.manifests()),1)
        self.assertIsNone(self.state())

    def test_source_symlink_is_not_followed(self):
        actual=self.fx.trace.with_suffix(".actual")
        data=self.fx.fixture_line("SYMLINK")
        actual.write_bytes(data)
        self.fx.trace.symlink_to(actual)
        self.assertEqual(self.m.capture(self.fx.args),1)
        self.assertEqual(self.fx.manifests(),[])
        self.assertEqual(actual.read_bytes(),data)

    def test_fsync_failure_after_ready_holds_on_restart(self):
        a=self.fx.fixture_line("FSYNC_FAULT")
        self.fx.trace.write_bytes(a)
        sync=self.m.os.fsync
        calls=[]
        def fail_fourth(fd):
            calls.append(fd)
            if len(calls)==4:
                raise OSError("synthetic sealed directory fsync failure")
            return sync(fd)
        self.m.os.fsync=fail_fourth
        try:
            with self.assertRaisesRegex(OSError,"synthetic sealed directory"):
                self.m.capture(self.fx.args)
        finally:
            self.m.os.fsync=sync
        self.assertIsNone(self.state())
        self.assertEqual(len(self.fx.manifests()),1)
        self.assertEqual(self.fx.module(False).capture(self.fx.args),1)
        self.assertIsNone(self.state())

    def test_fsync_failure_before_ready_holds_incomplete_seal(self):
        a=self.fx.fixture_line("INCOMPLETE")
        self.fx.trace.write_bytes(a)
        sync=self.m.os.fsync
        seen=[]
        def fail_second(fd):
            seen.append(fd)
            if len(seen)==2:
                raise OSError("synthetic manifest fsync failure")
            return sync(fd)
        self.m.os.fsync=fail_second
        try:
            with self.assertRaisesRegex(OSError,"synthetic manifest"):
                self.m.capture(self.fx.args)
        finally:
            self.m.os.fsync=sync
        self.assertIsNone(self.state())
        self.assertEqual(self.fx.module(False).capture(self.fx.args),1)
        self.assertIsNone(self.state())

    def test_seal_checkpoint_sync_calls_present(self):
        data=self.fx.fixture_line("FSYNC")
        self.fx.trace.write_bytes(data)
        seen=[]
        original=self.m.os.fsync
        def record(fd):
            seen.append(fd)
            return original(fd)
        self.m.os.fsync=record
        try:
            self.assertEqual(self.m.capture(self.fx.args),0)
        finally:
            self.m.os.fsync=original
        self.assertGreaterEqual(len(seen),6)
        self.assertEqual(self.captured(),data)

if __name__=="__main__":
    unittest.main(verbosity=2)