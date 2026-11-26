"""Group admission is a separate witness from installed signal handlers."""
import threading
import time
import unittest
from unittest.mock import Mock
from assistx import worker_supervision as s

class Stopped(Exception): pass

class GroupAdmissionTests(unittest.TestCase):
    def test_ack_does_not_open_queue(self):
        gate=threading.Event()
        calls=[]
        ready=Mock()
        class Base:
            _stop_requested=False
            def _install_signal_handlers(self):calls.append("installed")
            def check_for_suspension(self,burst):calls.append("broker")
        worker=s.readiness_worker_class(Base,ready,startup_gate=gate,stop_exception=Stopped,gate_timeout=.03)()
        worker._install_signal_handlers()
        ready.send_bytes.assert_called_once_with(b'RQ_READY_V1')
        with self.assertRaises(Stopped):worker.check_for_suspension(False)
        self.assertEqual(calls,["installed"])
    def test_open_gate_preserves_original_check(self):
        gate=threading.Event();gate.set()
        calls=[]
        class Base:
            _stop_requested=False
            def check_for_suspension(self,burst):calls.append(burst);return "original"
        worker=s.readiness_worker_class(Base,Mock(),startup_gate=gate,stop_exception=Stopped)()
        self.assertEqual(worker.check_for_suspension(True),"original")
        self.assertEqual(calls,[True])
    def test_stop_even_when_gate_open_never_calls_broker(self):
        gate=threading.Event();gate.set()
        class Base:
            _stop_requested=True
            def check_for_suspension(self,burst):raise AssertionError("broker reached")
        worker=s.readiness_worker_class(Base,Mock(),startup_gate=gate,stop_exception=Stopped)()
        with self.assertRaises(Stopped):worker.check_for_suspension(False)
    def test_waiter_released_only_after_gate_opens(self):
        gate=threading.Event();started=threading.Event();result=[]
        def run():
            started.set()
            result.append(s.await_start_gate(gate,lambda:False,timeout=.5,poll_seconds=.005))
        t=threading.Thread(target=run);t.start()
        try:
            self.assertTrue(started.wait(1));self.assertEqual(result,[])
            gate.set();t.join(1);self.assertEqual(result,[True])
        finally:
            gate.set();t.join(1)
    def test_closed_gate_times_out(self):
        self.assertFalse(s.await_start_gate(threading.Event(),lambda:False,timeout=.01,poll_seconds=.001))
    def test_stop_preempts_release(self):
        gate=threading.Event();gate.set()
        self.assertFalse(s.await_start_gate(gate,lambda:True,timeout=.01))
    def test_parent_refuses_incomplete_group(self):
        gate=threading.Event();p=Mock();p.is_alive.return_value=True
        self.assertFalse(s.open_group_admission(gate,[p],2,lambda:False))
        self.assertFalse(gate.is_set())
    def test_parent_refuses_dead_child_or_stop(self):
        gate=threading.Event();p=Mock();p.is_alive.return_value=False
        self.assertFalse(s.open_group_admission(gate,[p],1,lambda:False))
        p.is_alive.return_value=True
        self.assertFalse(s.open_group_admission(gate,[p],1,lambda:True))
        self.assertFalse(gate.is_set())
    def test_parent_opens_complete_live_group(self):
        gate=threading.Event();p=Mock();p.is_alive.return_value=True
        self.assertTrue(s.open_group_admission(gate,[p],1,lambda:False))
        self.assertTrue(gate.is_set())
