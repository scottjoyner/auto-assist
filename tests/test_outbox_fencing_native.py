"""Offline real SQLite regressions for worker ownership and concurrent enqueue."""
import concurrent.futures
import pathlib
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from assistx.outbox_client import OutboxClient

class ReleaseFencing(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db=str(pathlib.Path(tmp.name)/'outbox.db')
        self.client=OutboxClient(db_path=self.db,api_url='',auto_flush=False,claim_lease_s=1)
    def reclaimed(self):
        entry=self.client.enqueue({'event_id':'fencing-fixture'})
        first=self.client._claim_next()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE outbox SET claimed_at='2000-01-01T00:00:00+00:00' WHERE outbox_id=?",(entry.outbox_id,))
        second=self.client._claim_next()
        self.assertNotEqual(first.claim_owner,second.claim_owner)
        return first,second
    def test_stale_worker_cannot_resurrect_delivered(self):
        first,second=self.reclaimed()
        self.assertTrue(self.client._release(second,'delivered',2))
        self.assertFalse(self.client._release(first,'pending',1))
        self.assertEqual(self.client.get_stats()['delivered'],1)
    def test_stale_worker_cannot_rewrite_terminal_dead(self):
        first,second=self.reclaimed()
        self.assertTrue(self.client._release(second,'dead',10))
        self.assertFalse(self.client._release(first,'delivered',1))
        self.assertEqual(self.client.get_stats()['dead'],1)
    def test_claimless_release_is_refused(self):
        entry=self.client.enqueue({'event_id':'no-claim'})
        self.assertFalse(self.client._release(entry,'delivered',1))
        self.assertEqual(self.client.get_stats()['pending'],1)
    def test_repeated_release_is_refused(self):
        self.client.enqueue({'event_id':'repeat'})
        entry=self.client._claim_next()
        self.assertTrue(self.client._release(entry,'pending',1))
        self.assertFalse(self.client._release(entry,'dead',2))

class EnqueueFencing(unittest.TestCase):
    def test_cross_client_enqueue_uses_atomic_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(pathlib.Path(tmp)/'outbox.db')
            clients=[OutboxClient(db_path=db,api_url='',auto_flush=False) for _ in range(8)]
            gate=threading.Barrier(len(clients))
            def enqueue(c):
                gate.wait(timeout=5)
                return c.enqueue({'event_id':'same-event'}).outbox_id
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(clients)) as pool:
                ids=list(pool.map(enqueue,clients))
            self.assertEqual(len(set(ids)),1)
            self.assertEqual(clients[0].get_stats()['total'],1)
    def test_select_and_insert_share_write_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(pathlib.Path(tmp)/'outbox.db')
            c=OutboxClient(db_path=db,api_url='',auto_flush=False)
            traces=[];original=sqlite3.connect
            def connect(*args,**kwargs):
                conn=original(*args,**kwargs);conn.set_trace_callback(traces.append);return conn
            with patch('assistx.outbox_client.sqlite3.connect',side_effect=connect):
                c.enqueue({'event_id':'transaction-fixture'})
            select=next(i for i,s in enumerate(traces) if s.startswith('SELECT outbox_id'))
            insert=next(i for i,s in enumerate(traces) if s.startswith('INSERT INTO outbox'))
            begin=next(i for i,s in enumerate(traces) if s=='BEGIN IMMEDIATE')
            self.assertLess(begin,select);self.assertLess(select,insert)
