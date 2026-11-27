"""Offline regression for cooperative Hermes drain and self-task admission.

All client and fleet activity is mocked. No model requests or real task claims.
"""
import threading
import unittest
from unittest.mock import Mock, patch
from assistx.agents import hermes_agent_adapter as a

class HermesDrainTests(unittest.TestCase):
    def setUp(self):
        self.stop=threading.Event()
        self.client=Mock()
        self.fleet=Mock()
        self.fleet.healthy_node_count.return_value=4
        self.process=Mock()
        for name,value in [('AssistXClient',Mock(return_value=self.client)),('fleet',self.fleet),('process_task',self.process),('SELFTASK_BULK_MODELS',[]),('_SELFTASK_ACTIVE',{}),('SELFTASK_CONCURRENCY',4)]:
            p=patch.object(a,name,value);p.start();self.addCleanup(p.stop)
    def test_preexisting_stop_does_not_initialize_or_poll(self):
        self.stop.set()
        a.run_loop(stopping=self.stop.is_set)
        self.fleet.start.assert_not_called()
        a.AssistXClient.assert_not_called()
    def test_stop_during_poll_does_not_process_returned_tasks(self):
        def poll(**kwargs):self.stop.set();return [{'id':'fixture'}]
        self.client.poll_tasks.side_effect=poll
        a.run_loop(stopping=self.stop.is_set)
        self.process.assert_not_called()
    def test_stop_after_first_task_does_not_claim_next(self):
        self.client.poll_tasks.return_value=[{'id':'first'},{'id':'second'}]
        self.process.side_effect=lambda *_:self.stop.set()
        a.run_loop(stopping=self.stop.is_set)
        self.process.assert_called_once_with(self.client,{'id':'first'})
    def test_zero_healthy_nodes_means_zero_launches(self):
        self.fleet.healthy_node_count.return_value=0
        with patch.object(a.threading,'Thread') as t:
            self.assertEqual(a._launch_selftasks(self.client,4),0)
            t.assert_not_called()
    def test_stop_disables_selftask_admission(self):
        self.stop.set()
        with patch.object(a.threading,'Thread') as t:
            self.assertEqual(a._launch_selftasks(self.client,4,stopping=self.stop.is_set),0)
            t.assert_not_called()
    def test_failed_thread_start_releases_reservation(self):
        t=Mock();t.start.side_effect=RuntimeError('synthetic start failure')
        with patch.object(a.threading,'Thread',return_value=t):
            with self.assertRaises(RuntimeError):a._launch_selftasks(self.client,1)
        self.assertEqual(a._SELFTASK_ACTIVE,{})
    def test_drain_waits_for_existing_selftask(self):
        entered=threading.Event();release=threading.Event();drained=threading.Event()
        def work(*args,**kwargs):
            entered.set()
            release.wait(2)
        with patch.object(a,'process_self_task',side_effect=work):
            try:
                self.assertEqual(a._launch_selftasks(self.client,1),1)
                self.assertTrue(entered.wait(1))
                def drain():a._drain_selftasks();drained.set()
                t=threading.Thread(target=drain,daemon=True);t.start()
                self.assertFalse(drained.wait(.05))
                release.set()
                self.assertTrue(drained.wait(1));t.join(1)
                self.assertEqual(a._SELFTASK_ACTIVE,{})
            finally:release.set()
    def test_two_concurrent_launchers_respect_shared_capacity(self):
        entered=threading.Event();release=threading.Event()
        def work(*args,**kwargs):entered.set();release.wait(2)
        self.fleet.healthy_node_count.return_value=1
        launches=[];barrier=threading.Barrier(3)
        def launch():barrier.wait();launches.append(a._launch_selftasks(self.client,1))
        with patch.object(a,'process_self_task',side_effect=work):
            try:
                t1=threading.Thread(target=launch);t2=threading.Thread(target=launch)
                t1.start();t2.start();barrier.wait();t1.join(1);t2.join(1)
                self.assertEqual(sorted(launches),[0,1]);self.assertTrue(entered.wait(1))
            finally:
                release.set()
                if hasattr(a,'_drain_selftasks'):a._drain_selftasks()
