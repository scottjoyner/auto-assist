"""Actual pinned in-memory EventOutbox; no real router or dispatch."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research"))
import fleet_router_observation_journal as journal
from fleet_router_request_observation import build_observation, observation_key
from fleet_router_publication_witness import initialize_acknowledgments, acknowledge, EVENT_TYPE

def load_outbox():
    raw=(Path(os.environ["ROUTER_SOURCE_ROOT"])/"src/auto_router/event_outbox.py").read_bytes()
    if hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()!="024bf48c7f2cf5f97965c5018e69711440abb939":
        raise RuntimeError("source pin mismatch")
    m=ModuleType("fixture_outbox")
    sys.modules[m.__name__]=m
    exec(compile(raw,"outbox","exec"),m.__dict__)
    return m

class WitnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.module=load_outbox()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"journal.db"
        journal.initialize(self.path)
        initialize_acknowledgments(self.path)
        self.outbox=self.module.EventOutbox("sqlite:///:memory:")
        self.conn=self.outbox._memory_connection
        self.addCleanup(self.conn.close)
        self.oid=journal.reserve(self.path,"a")
        self.row=self.build()
        journal.record(self.path,"a",self.row)
    def build(self,usage=None):
        return build_observation(observation_id=self.oid,
            request=SimpleNamespace(request_id="r",metadata={}),provider="p",model="m",
            stage="primary",status_code=200,usage=usage)
    def enqueue(self,row=None,**kw):
        row=self.row if row is None else row
        return self.outbox.enqueue(self.module.OutboxEvent(event_type=kw.pop("event_type",EVENT_TYPE),
            idempotency_key=observation_key(row),payload=row,**kw))
    def ack(self):return acknowledge(self.path,"a",self.conn)
    def test_interrupted_publication_recovery(self):
        event=self.enqueue() # interruption before journal acknowledgment
        self.assertEqual(self.ack(),"LOCAL_OUTBOX_MATCH_RECORDED")
        self.assertEqual(self.ack(),"MATCH_REPLAY")
        self.assertEqual(self.enqueue(),event)
        self.assertEqual(len(self.outbox.recent()),1)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("select event_id from observation_publication_witness").fetchone()[0],event)
    def test_missing_and_pending_no_implicit_publication(self):
        self.assertEqual(self.ack(),"HOLD_OUTBOX_ROW_UNAVAILABLE")
        journal.reserve(self.path,"b")
        self.assertEqual(acknowledge(self.path,"b",self.conn),"HOLD_NO_RECORDED_OBSERVATION")
        self.assertEqual(self.outbox.recent(),[])
    def test_outbox_silent_conflict_not_acknowledged(self):
        wrong=self.build({"completion_tokens":9})
        self.enqueue(wrong)
        self.enqueue()
        self.assertEqual(self.ack(),"HOLD_OUTBOX_PAYLOAD")
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("select count(*) from observation_publication_witness").fetchone()[0],0)
    def test_pruned_after_witness_holds(self):
        eid=self.enqueue()
        self.assertEqual(self.ack(),"LOCAL_OUTBOX_MATCH_RECORDED")
        self.outbox.mark_delivered(eid)
        self.outbox.prune_terminal(keep_delivered=0,keep_dead_letter=0)
        self.assertEqual(self.ack(),"HOLD_PRIOR_WITNESS_MISSING")
        self.assertEqual(self.outbox.recent(),[])
    def test_changed_event_identity_holds(self):
        eid=self.enqueue()
        self.assertEqual(self.ack(),"LOCAL_OUTBOX_MATCH_RECORDED")
        with self.conn:self.conn.execute("update event_outbox set event_id='replacement' where event_id=?",(eid,))
        self.assertEqual(self.ack(),"HOLD_WITNESS_CHANGED")
    def test_journal_conflict_invalidates_match(self):
        self.enqueue()
        self.assertEqual(self.ack(),"LOCAL_OUTBOX_MATCH_RECORDED")
        journal.record(self.path,"a",self.build({"completion_tokens":4}))
        self.assertEqual(self.ack(),"HOLD_JOURNAL_CONFLICT")
    def test_wrong_namespace_and_duplicate_keys(self):
        self.enqueue(event_type="other")
        self.assertEqual(self.ack(),"HOLD_OUTBOX_IDENTITY")
        with self.conn:self.conn.execute("update event_outbox set event_type=?,payload_json=?",(EVENT_TYPE,'{"x":1,"x":2}'))
        self.assertEqual(self.ack(),"HOLD_OUTBOX_PAYLOAD")
    def test_delivery_status_not_used_as_ack(self):
        eid=self.enqueue()
        self.outbox.mark_failed(eid,"fixture",retry=False)
        self.assertEqual(self.ack(),"LOCAL_OUTBOX_MATCH_RECORDED")
        self.assertEqual(self.outbox.recent()[0]["status"],"dead_letter")
    def test_explicit_create_only_extension(self):
        with self.assertRaises(sqlite3.OperationalError):initialize_acknowledgments(self.path)
    def test_witness_reopen_and_readonly_outbox(self):
        self.enqueue()
        self.conn.execute("PRAGMA query_only=ON")
        self.assertEqual(self.ack(),"LOCAL_OUTBOX_MATCH_RECORDED")
        self.assertEqual(self.ack(),"MATCH_REPLAY")

if __name__=="__main__":unittest.main(verbosity=2)
