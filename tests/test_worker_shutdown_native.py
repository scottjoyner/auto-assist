"""Offline supervisor shutdown tests; no Redis, network or real fleet jobs."""
import importlib.util
import pathlib
import signal
import sys
import time
import unittest
from unittest.mock import patch

# Load the standalone supervisor (no production worker imports).
path=pathlib.Path(__file__).resolve().parents[1]/'src/assistx/worker_supervision.py'

class Child:
    def __init__(self,exitcode=None,finish_on_signal=True):
        self.exitcode=exitcode
        self.finish_on_signal=finish_on_signal
        self.terminated=0
        self.joins=[]
    def is_alive(self):return self.exitcode is None
    def terminate(self):
        self.terminated+=1
        if self.finish_on_signal:self.exitcode=0
    def join(self,timeout=None):self.joins.append(timeout)

class ShutdownTests(unittest.TestCase):
    def load(self):
        spec=importlib.util.spec_from_file_location('worker_supervision_fixture',path)
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
    def test_term_forwarded_once_to_every_live_child(self):
        m=self.load();a=Child();b=Child();done=Child(0)
        self.assertEqual(m.supervise([a,b,done],lambda:True,grace_seconds=.02),0)
        self.assertEqual([x.terminated for x in (a,b,done)],[1,1,0])
    def test_child_failure_propagated(self):
        m=self.load();self.assertEqual(m.supervise([Child(3),Child(0)],lambda:False),3)
    def test_stuck_child_is_reported_not_killed(self):
        m=self.load();a=Child(finish_on_signal=False)
        self.assertEqual(m.supervise([a],lambda:True,grace_seconds=.001,poll_seconds=.001),75)
        self.assertEqual(a.terminated,1)
    def test_shutdown_handler_is_restored(self):
        m=self.load();before=signal.getsignal(signal.SIGTERM)
        with m.shutdown_flag() as stopping:
            self.assertFalse(stopping())
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM,None)
            self.assertTrue(stopping())
        self.assertEqual(signal.getsignal(signal.SIGTERM),before)
    def test_no_double_term_after_repeated_stop_signal(self):
        m=self.load();a=Child(finish_on_signal=False)
        self.assertEqual(m.supervise([a],lambda:True,grace_seconds=.002,poll_seconds=.001),75)
        self.assertEqual(a.terminated,1)
