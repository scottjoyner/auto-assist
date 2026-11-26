"""All managed execution paths must wait behind the same startup gate."""
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from assistx import worker as w, recovery_island_control as r
from assistx.agents import hermes_agent_adapter as a
from assistx import paperclip_poller as paperclip, strict_executor_adapter as strict

class UnifiedAdmissionTests(unittest.TestCase):
    def test_health_closed_gate_or_drain_returns_503(self):
        stop=threading.Event();gate=threading.Event()
        self.assertTrue(w._health_unavailable(stop,gate))
        gate.set();self.assertFalse(w._health_unavailable(stop,gate))
        stop.set();self.assertTrue(w._health_unavailable(stop,gate))
    def exercise_pollers(self,release):
        stop=threading.Event();gate=threading.Event()
        direct=threading.Event();paper=threading.Event()
        with patch.object(w,'settings',SimpleNamespace(execution_backend='auto')),patch.object(a,'run_loop',side_effect=lambda **kw:direct.set()),patch.object(strict,'install_strict_executor_adapter'),patch.object(paperclip,'paperclip_poll_job',side_effect=paper.set):
            threads=w._start_execution_pollers(stop,startup_gate=gate)
            try:
                self.assertEqual(len(threads),2)
                self.assertFalse(direct.wait(.06));self.assertFalse(paper.is_set())
                if release:
                    gate.set();self.assertTrue(direct.wait(1));self.assertTrue(paper.wait(1))
                else:
                    stop.set()
                    for t in threads:t.join(1)
                    self.assertFalse(direct.is_set());self.assertFalse(paper.is_set())
            finally:
                stop.set()
                for t in threads:t.join(1)
            self.assertTrue(all(not t.is_alive() for t in threads))
    def test_pollers_do_not_run_before_group_release(self):self.exercise_pollers(True)
    def test_cancelled_pollers_never_dispatch(self):self.exercise_pollers(False)
    def exercise_recovery(self,release):
        stop=threading.Event();gate=threading.Event();tick=threading.Event()
        fixture=Mock();fixture.tick.side_effect=lambda:(tick.set() or {})
        with patch.dict(os.environ,{'ASSISTX_RECOVERY_ISLAND_DISPATCH_ENABLED':'true','ASSISTX_RECOVERY_ISLAND_DISPATCH_INTERVAL_SECONDS':'60'}),patch.object(r,'RecoveryIslandDispatcher',return_value=fixture):
            t=r.start_recovery_island_dispatcher(stop_event=stop,startup_gate=gate)
            try:
                self.assertFalse(tick.wait(.06))
                if release:
                    gate.set();self.assertTrue(tick.wait(1))
                else:
                    stop.set();t.join(1);fixture.tick.assert_not_called()
            finally:stop.set();t.join(1)
            self.assertFalse(t.is_alive())
    def test_recovery_waits_for_release(self):self.exercise_recovery(True)
    def test_cancelled_recovery_never_ticks(self):self.exercise_recovery(False)
    def test_enabled_controller_failure_is_not_optional(self):
        with patch.dict(os.environ,{'ASSISTX_RECOVERY_ISLAND_DISPATCH_ENABLED':'true'}):
            self.assertTrue(w._recovery_required())
        with patch.dict(os.environ,{'ASSISTX_RECOVERY_ISLAND_DISPATCH_ENABLED':'false'}):
            self.assertFalse(w._recovery_required())
    def test_health_listener_must_be_ready_before_release(self):
        ready=threading.Event();thread=Mock();thread.is_alive.return_value=True
        self.assertFalse(w._wait_health_listener(ready,thread,lambda:False,timeout=.01))
        ready.set()
        self.assertTrue(w._wait_health_listener(ready,thread,lambda:False,timeout=.01))
        self.assertFalse(w._wait_health_listener(ready,thread,lambda:True,timeout=.01))
        thread.is_alive.return_value=False
        self.assertFalse(w._wait_health_listener(ready,thread,lambda:False,timeout=.01))
