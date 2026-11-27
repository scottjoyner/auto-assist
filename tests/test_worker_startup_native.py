"""Startup control regressions using synthetic children, no broker."""
import multiprocessing as mp
import os
import signal
import unittest
from unittest.mock import Mock
from assistx import worker_supervision as s

class Child:
    pid=None
    def __init__(self, action=None):
        self.action=action
    def start(self):
        if self.action:self.action()
        self.pid=123

class StartupTests(unittest.TestCase):
    def test_preexisting_stop_prevents_spawn(self):
        ctx=Mock()
        children,error=s.launch_children(ctx,32,lambda:None,lambda i:(),lambda:True)
        self.assertEqual((children,error),([],None))
        ctx.Process.assert_not_called()
    def test_stop_during_spawn_preserves_started_child(self):
        ctx=Mock();state=[False]
        child=Child(lambda:state.__setitem__(0,True));ctx.Process.return_value=child
        children,error=s.launch_children(ctx,32,lambda:None,lambda i:(),lambda:state[0])
        self.assertEqual(children,[child]);self.assertIsNone(error)
        self.assertEqual(ctx.Process.call_count,1)
    def test_partial_spawn_failure_preserves_prior_children(self):
        ctx=Mock()
        first=Child()
        def fail():raise OSError('synthetic fork failure')
        ctx.Process.side_effect=[first,Child(fail)]
        children,error=s.launch_children(ctx,32,lambda:None,lambda i:(),lambda:False)
        self.assertEqual(children,[first]);self.assertEqual(error,'OSError')
    def test_non_os_spawn_error_preserves_prior_children(self):
        ctx=Mock()
        first=Child()
        def fail():raise ValueError('synthetic argument failure')
        ctx.Process.side_effect=[first,Child(fail)]
        children,error=s.launch_children(ctx,2,lambda:None,lambda i:(),lambda:False)
        self.assertEqual(children,[first]);self.assertEqual(error,'ValueError')

    def test_start_error_after_pid_assignment_retains_child(self):
        ctx=Mock();child=Child()
        def fail():
            child.pid=123
            raise ValueError('synthetic post-start failure')
        child.action=fail
        ctx.Process.return_value=child
        children,error=s.launch_children(ctx,1,lambda:None,lambda i:(),lambda:False)
        self.assertEqual(children,[child]);self.assertEqual(error,'ValueError')

    def test_parent_mask_restored_after_start_failure(self):
        ctx=Mock()
        def fail():raise RuntimeError('synthetic start failure')
        ctx.Process.return_value=Child(fail)
        before=signal.pthread_sigmask(signal.SIG_BLOCK,[])
        children,error=s.launch_children(ctx,1,lambda:None,lambda i:(),lambda:False)
        self.assertEqual(error,'RuntimeError')
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK,[]),before)
    def test_real_fork_child_does_not_inherit_parent_shutdown_handler(self):
        ctx=mp.get_context('fork')
        receive,send=ctx.Pipe(duplex=False)
        def child():
            mask=signal.pthread_sigmask(signal.SIG_BLOCK,[])
            send.send((signal.getsignal(signal.SIGTERM)==signal.SIG_DFL,
                       signal.getsignal(signal.SIGINT)==signal.SIG_DFL,
                       signal.SIGTERM in mask,signal.SIGINT in mask))
            send.close()
        with s.shutdown_flag() as stopping:
            processes,error=s.launch_children(ctx,1,child,lambda i:(),stopping)
            self.assertIsNone(error)
            p=processes[0]
            send.close()
            try:
                self.assertTrue(receive.poll(2))
                self.assertEqual(receive.recv(),(True,True,False,False))
                p.join(2);self.assertEqual(p.exitcode,0)
            finally:
                receive.close()
                if p.is_alive():p.terminate();p.join(2)
