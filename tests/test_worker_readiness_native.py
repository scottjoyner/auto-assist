"""Readiness is an explicit signal-handler witness, not mere PID existence."""
import multiprocessing as mp
import unittest
from unittest.mock import Mock
from assistx import worker_supervision as s

class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.ctx=mp.get_context('fork')
        self.pairs=[self.ctx.Pipe(duplex=False) for _ in range(2)]
        self.addCleanup(self.close_pipes)
        self.children=[Mock(),Mock()]
        for p in self.children:p.is_alive.return_value=True
    def close_pipes(self):
        for pair in self.pairs:
            for end in pair:end.close()
    def wait(self,stopping=lambda:False):
        return s.wait_child_readiness(self.children,[r for r,w in self.pairs],stopping,timeout=.03,poll_seconds=.001)
    def test_all_children_ack_required(self):
        for r,w in self.pairs:w.send_bytes(b'RQ_READY_V1')
        self.assertEqual(self.wait(),None)
    def test_partial_ack_times_out(self):
        self.pairs[0][1].send_bytes(b'RQ_READY_V1')
        self.assertEqual(self.wait(),'readiness_timeout')
    def test_wrong_ack_is_fault(self):
        self.pairs[0][1].send_bytes(b'wrong')
        self.assertEqual(self.wait(),'readiness_invalid')
    def test_dead_child_is_fault_even_with_ack(self):
        for r,w in self.pairs:w.send_bytes(b'RQ_READY_V1')
        self.children[1].is_alive.return_value=False
        self.assertEqual(self.wait(),'child_exited')
    def test_stop_preempts_ready(self):
        for r,w in self.pairs:w.send_bytes(b'RQ_READY_V1')
        self.assertEqual(self.wait(lambda:True),'shutdown_requested')
    def test_handler_installation_precedes_ack(self):
        order=[]
        class FakeRQ:
            def _install_signal_handlers(self):order.append('installed')
        ready=Mock()
        ready.send_bytes.side_effect=lambda data:order.append(data)
        s.readiness_worker_class(FakeRQ,ready)()._install_signal_handlers()
        self.assertEqual(order,['installed',b'RQ_READY_V1'])
        ready.close.assert_called_once()
    def test_install_failure_never_acks(self):
        class FakeRQ:
            def _install_signal_handlers(self):raise RuntimeError('synthetic')
        ready=Mock()
        with self.assertRaises(RuntimeError):s.readiness_worker_class(FakeRQ,ready)()._install_signal_handlers()
        ready.send_bytes.assert_not_called()
