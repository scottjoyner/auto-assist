"""Synthetic capacity loss and health response regressions, no live signals."""
import json, threading, unittest
from assistx import worker as w, worker_supervision as s

class Child:
    def __init__(self,exitcode=None):
        self.exitcode=exitcode;self.signals=0
    def is_alive(self):return self.exitcode is None
    def terminate(self):self.signals+=1;self.exitcode=0
    def join(self,timeout=None):pass

class CapacityTests(unittest.TestCase):
    def test_partial_loss_drains_remaining_children(self):
        stop=threading.Event();alive=Child();dead=Child(3)
        self.assertEqual(s.supervise([dead,alive],lambda:False,fail_on_child_exit=True,on_shutdown=stop.set,poll_seconds=.001),3)
        self.assertTrue(stop.is_set());self.assertEqual(alive.signals,1)
    def test_unexpected_clean_exit_is_failure(self):
        stop=threading.Event()
        self.assertEqual(s.supervise([Child(0),Child()],lambda:False,fail_on_child_exit=True,on_shutdown=stop.set,poll_seconds=.001),70)
        self.assertTrue(stop.is_set())
    def test_all_children_gone_closes_admission(self):
        stop=threading.Event()
        self.assertEqual(s.supervise([Child(0)],lambda:False,fail_on_child_exit=True,on_shutdown=stop.set,poll_seconds=.001),70)
        self.assertTrue(stop.is_set())
    def test_explicit_shutdown_is_not_capacity_failure(self):
        self.assertEqual(s.supervise([Child(0),Child()],lambda:True,fail_on_child_exit=True,poll_seconds=.001),0)
    def test_dead_poller_triggers_drain(self):
        stop=threading.Event();child=Child()
        self.assertEqual(s.supervise([child],lambda:False,faulted=lambda:True,on_shutdown=stop.set,poll_seconds=.001),70)
        self.assertTrue(stop.is_set());self.assertEqual(child.signals,1)
    def test_health_response_length_and_status(self):
        for draining,expected in ((False,b'200 OK'),(True,b'503 Service Unavailable')):
            head,body=w._health_response(draining).split(b'\r\n\r\n')
            self.assertIn(expected,head)
            self.assertIn(b'Content-Length: '+str(len(body)).encode(),head)
            self.assertEqual(json.loads(body)['ok'],not draining)
