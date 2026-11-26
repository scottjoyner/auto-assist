"""Offline acknowledgement and SQLite startup transaction regressions."""
import concurrent.futures
import io
import pathlib
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from assistx.outbox_client import OutboxClient

class AcknowledgementTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.client=OutboxClient(db_path=str(pathlib.Path(t.name)/'outbox.db'),api_url='http://assistx.invalid',auto_flush=False)
        self.entry=self.client.enqueue({'event_id':'fixture'})
    def deliver(self,raw):
        with patch('assistx.outbox_client.urlopen',return_value=io.BytesIO(raw)):
            return self.client._deliver(self.entry)
    def test_non_object_is_not_acknowledged(self):
        for raw in (b'[]',b'null',b'true',b'1'):
            with self.subTest(raw=raw):self.assertFalse(self.deliver(raw))
    def test_truthy_non_boolean_is_not_acknowledged(self):
        for raw in (b'{"accepted":"false"}',b'{"accepted":1}',b'{"accepted":[]}',b'{"accepted":{}}'):
            with self.subTest(raw=raw):self.assertFalse(self.deliver(raw))
    def test_invalid_utf8_is_not_acknowledged(self):
        self.assertFalse(self.deliver(b'\xff'))
    def test_oversized_ack_is_not_acknowledged(self):
        self.assertFalse(self.deliver(b'{"accepted":true,"extra":"'+b'x'*65536+b'"}'))
    def test_duplicate_ack_field_is_not_acknowledged(self):
        self.assertFalse(self.deliver(b'{"accepted":false,"accepted":true}'))
    def test_valid_ack_and_deduped_ack(self):
        self.assertTrue(self.deliver(b'{"accepted":true,"event_id":"fixture"}'))
        self.assertTrue(self.deliver(b'{"accepted":true,"event_id":"existing","deduped":true}'))
        self.assertFalse(self.deliver(b'{"accepted":false}'))

class MigrationTests(unittest.TestCase):
    def test_migration_reserves_write_transaction_before_schema_probe(self):
        with tempfile.TemporaryDirectory() as t:
            db=str(pathlib.Path(t)/'outbox.db');traces=[];original=sqlite3.connect
            def connect(*args,**kwargs):
                c=original(*args,**kwargs);c.set_trace_callback(traces.append);return c
            with patch('assistx.outbox_client.sqlite3.connect',side_effect=connect):
                OutboxClient(db_path=db,api_url='',auto_flush=False)
            probe=next(i for i,s in enumerate(traces) if 'PRAGMA table_info(outbox)' in s)
            begin=next(i for i,s in enumerate(traces) if s=='BEGIN IMMEDIATE')
            self.assertLess(begin,probe)
    def test_parallel_legacy_startup_preserves_event(self):
        with tempfile.TemporaryDirectory() as t:
            db=str(pathlib.Path(t)/'legacy.db')
            with sqlite3.connect(db) as c:
                c.execute("CREATE TABLE outbox (outbox_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,payload_json TEXT NOT NULL,attempt_count INTEGER DEFAULT 0,last_attempt_at TEXT,status TEXT DEFAULT 'pending',created_at TEXT NOT NULL)")
                c.execute("INSERT INTO outbox VALUES ('legacy','event','{}',0,NULL,'pending','2026-11-25T00:00:00+00:00')")
            gate=threading.Barrier(6)
            def create(_):
                gate.wait(timeout=5)
                return OutboxClient(db_path=db,api_url='',auto_flush=False).get_stats()['pending']
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                self.assertEqual(list(pool.map(create,range(6))),[1]*6)
            with sqlite3.connect(db) as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
                self.assertTrue({'claim_owner','claimed_at'}<={r[1] for r in c.execute('PRAGMA table_info(outbox)')})
