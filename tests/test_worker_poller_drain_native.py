"""Exercise real thread drain wiring with synthetic dispatch callbacks only."""
import os, threading, unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from assistx import worker as w, recovery_island_control as r, worker_supervision as s
from assistx.agents import hermes_agent_adapter as a
from assistx import paperclip_poller as paperclip, strict_executor_adapter as strict

class PollerDrainTests(unittest.TestCase):
    def test_both_execution_pollers_share_stop_event(self):
        stop=threading.Event()
        started=threading.Event()
        paper=threading.Event()
        def direct(*,stopping):
            started.set()
            while not stopping():
                stop.wait(.01)
        with patch.object(w,'settings',SimpleNamespace(execution_backend='auto')), patch.object(a,'run_loop',side_effect=direct), patch.object(strict,'install_strict_executor_adapter'), patch.object(paperclip,'paperclip_poll_job',side_effect=paper.set):
            threads=w._start_execution_pollers(stop)
            try:
                self.assertEqual(len(threads),2)
                self.assertTrue(started.wait(1))
                self.assertTrue(paper.wait(1))
                self.assertTrue(all(not t.daemon for t in threads))
            finally:
                stop.set()
                for t in threads:t.join(1)
            self.assertTrue(all(not t.is_alive() for t in threads))
    def test_recovery_dispatcher_interrupts_interval(self):
        stop=threading.Event();tick=threading.Event()
        fixture=Mock()
        def run_tick():tick.set();return {}
        fixture.tick.side_effect=run_tick
        with patch.dict(os.environ,{'ASSISTX_RECOVERY_ISLAND_DISPATCH_ENABLED':'true','ASSISTX_RECOVERY_ISLAND_DISPATCH_INTERVAL_SECONDS':'60'}),patch.object(r,'RecoveryIslandDispatcher',return_value=fixture):
            t=r.start_recovery_island_dispatcher(stop_event=stop)
            try:
                self.assertTrue(tick.wait(1));self.assertFalse(t.daemon)
            finally:stop.set();t.join(1)
            self.assertFalse(t.is_alive());self.assertEqual(fixture.tick.call_count,1)
    def test_preclosed_recovery_dispatcher_never_ticks(self):
        stop=threading.Event();stop.set();fixture=Mock()
        with patch.dict(os.environ,{'ASSISTX_RECOVERY_ISLAND_DISPATCH_ENABLED':'true'}),patch.object(r,'RecoveryIslandDispatcher',return_value=fixture):
            t=r.start_recovery_island_dispatcher(stop_event=stop)
            t.join(1);self.assertFalse(t.is_alive());fixture.tick.assert_not_called()
    def test_supervisor_closes_admission_before_child_term(self):
        order=[]
        class Child:
            exitcode=None
            def is_alive(self):return self.exitcode is None
            def terminate(self):order.append('terminate');self.exitcode=0
            def join(self,timeout=None):pass
        self.assertEqual(s.supervise([Child(),Child()],lambda:True,on_shutdown=lambda:order.append('close'),poll_seconds=.001),0)
        self.assertEqual(order,['close','terminate','terminate'])
