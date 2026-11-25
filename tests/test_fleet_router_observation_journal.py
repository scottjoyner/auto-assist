import concurrent.futures
import copy
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research"))
import fleet_router_observation_journal as journal
from fleet_router_request_observation import build_observation

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=self.root/"journal.db"
        journal.initialize(self.path)
    def build(self,oid,usage=None):
        return build_observation(observation_id=oid,
            request=SimpleNamespace(request_id="r",metadata={}),
            provider="p",model="m",stage="primary",status_code=200,usage=usage)
    def test_restart_reservation_and_record(self):
        oid=journal.reserve(self.path,"attempt")
        row=self.build(oid,{"prompt_tokens":3,"completion_tokens":2})
        self.assertEqual(journal.record(self.path,"attempt",row),"RECORDED")
        code="import sys; sys.path.insert(0,sys.argv[1]); import fleet_router_observation_journal as j; print(j.reserve(sys.argv[2],'attempt'))"
        r=subprocess.run([sys.executable,"-B","-c",code,str(ROOT/"research"),str(self.path)],
                         capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(r.stdout.strip(),oid)
        self.assertEqual(journal.record(self.path,"attempt",row),"EXACT_REPLAY")
        s=journal.status(self.path)
        self.assertEqual((s["reserved"],s["pending"],s["recorded"]),(1,0,1))
        self.assertIsNone(s["actual_endpoint_calls"])
    def test_distinct_allocations(self):
        a=journal.reserve(self.path,"a")
        b=journal.reserve(self.path,"b")
        self.assertNotEqual(a,b)
        self.assertEqual(journal.status(self.path)["pending"],2)
    def test_conflict_sticky_preserves_original(self):
        oid=journal.reserve(self.path,"a")
        a=self.build(oid);b=self.build(oid,{"completion_tokens":4})
        journal.record(self.path,"a",a)
        with sqlite3.connect(self.path) as c:
            before=c.execute("select payload_sha256,payload_json from observation_journal").fetchone()
        self.assertEqual(journal.record(self.path,"a",b),"HOLD_CONFLICT")
        self.assertEqual(journal.record(self.path,"a",a),"HOLD_PRIOR_CONFLICT")
        with self.assertRaisesRegex(journal.JournalHold,"prior_observation_conflict"):
            journal.reserve(self.path,"a")
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("select payload_sha256,payload_json from observation_journal").fetchone(),before)
        self.assertEqual(journal.status(self.path)["decision"],"HOLD")
    def test_concurrent_allocation_and_record(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids=list(pool.map(lambda _:journal.reserve(self.path,"same"),range(8)))
            self.assertEqual(len(set(ids)),1)
            row=self.build(ids[0])
            outcomes=list(pool.map(lambda _:journal.record(self.path,"same",row),range(8)))
        self.assertEqual(outcomes.count("RECORDED"),1)
        self.assertEqual(outcomes.count("EXACT_REPLAY"),7)
    def test_concurrent_conflict(self):
        oid=journal.reserve(self.path,"a")
        rows=[self.build(oid),self.build(oid,{"completion_tokens":4})]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(lambda r:journal.record(self.path,"a",r),rows))
        self.assertEqual(set(outcomes),{"RECORDED","HOLD_CONFLICT"})
        self.assertEqual(journal.status(self.path)["conflicted"],1)
    def test_unknown_allocation_and_wrong_id(self):
        oid=journal.reserve(self.path,"a")
        with self.assertRaisesRegex(journal.JournalHold,"allocation_missing"):
            journal.record(self.path,"b",self.build(oid))
        with self.assertRaisesRegex(journal.JournalHold,"allocation_identity_mismatch"):
            journal.record(self.path,"a",self.build("other"))
        self.assertEqual(journal.status(self.path)["pending"],1)
    def test_no_clobber_and_missing_no_recreate(self):
        before=self.path.read_bytes()
        with self.assertRaises(FileExistsError):journal.initialize(self.path)
        self.assertEqual(self.path.read_bytes(),before)
        missing=self.root/"missing.db"
        with self.assertRaises(FileNotFoundError):journal.reserve(missing,"a")
        self.assertFalse(missing.exists())
    def test_private_file_and_directory(self):
        alias=self.root/"alias.db";alias.symlink_to(self.path)
        with self.assertRaises(journal.JournalHold):journal.reserve(alias,"a")
        self.path.chmod(0o644)
        with self.assertRaises(journal.JournalHold):journal.reserve(self.path,"a")
        self.path.chmod(0o600)
        self.root.chmod(0o755)
        try:
            with self.assertRaises(journal.JournalHold):journal.reserve(self.path,"a")
        finally:self.root.chmod(0o700)
    def test_bounds_and_authority_flags(self):
        oid=journal.reserve(self.path,"a")
        with patch.object(journal,"MAX_ROWS",1):
            with self.assertRaisesRegex(journal.JournalHold,"journal_row_limit"):
                journal.reserve(self.path,"b")
            self.assertEqual(journal.reserve(self.path,"a"),oid)
        row=self.build(oid);row["physical_execution_verified"]=True
        with self.assertRaises(journal.JournalHold):journal.record(self.path,"a",row)
        with patch.object(journal,"MAX_PAYLOAD_BYTES",1):
            with self.assertRaisesRegex(journal.JournalHold,"observation_size_limit"):
                journal.record(self.path,"a",self.build(oid))
        self.assertEqual(journal.status(self.path)["pending"],1)
    def test_rollback_on_failed_transaction(self):
        with self.assertRaises(RuntimeError):
            with journal._transaction(self.path) as c:
                c.execute("insert into observation_journal(allocation_digest,observation_id) values ('x','y')")
                raise RuntimeError("fixture")
        self.assertEqual(journal.status(self.path)["reserved"],0)
    def test_version_mismatch(self):
        with sqlite3.connect(self.path) as c:c.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(journal.JournalHold,"unsupported_journal_version"):
            journal.reserve(self.path,"a")

if __name__=="__main__":unittest.main(verbosity=2)
