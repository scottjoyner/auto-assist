"""Offline supervisor shutdown tests; no Redis, network or real fleet jobs."""
import importlib.util
import os
import subprocess
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

    def test_real_parent_sigterm_drains_two_children(self):
        self.assert_real_drain(2)

    def test_real_parent_sigterm_drains_32_children(self):
        self.assert_real_drain(32)

    def assert_real_drain(self,count):
        # No live worker or broker: isolated subprocess, real OS signals.
        program = r"""
import importlib.util,multiprocessing as mp,signal,sys,time
spec=importlib.util.spec_from_file_location('supervision',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def child(ready):
    def stop(signum,frame):raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop)
    ready.send(True)
    ready.close()
    while True:time.sleep(.05)
ctx=mp.get_context("fork")
channels=[ctx.Pipe(duplex=False) for _ in range(int(sys.argv[2]))]
children=[ctx.Process(target=child,args=(send,)) for recv,send in channels]
for p in children:p.start()
for recv,send in channels:send.close()
if not all(recv.poll(2) and recv.recv() is True for recv,send in channels):raise SystemExit(2)
for recv,send in channels:recv.close()
with m.shutdown_flag() as stopping:
    print("READY",flush=True)
    rc=m.supervise(children,stopping,grace_seconds=2,poll_seconds=.02)
print("DRAINED",rc,len(children),flush=True)
raise SystemExit(rc)
"""
        proc=subprocess.Popen([sys.executable,'-B','-c',program,str(path),str(count)],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              text=True,start_new_session=True)
        try:
            import select
            ready,_,_=select.select([proc.stdout],[],[],4)
            self.assertTrue(ready)
            marker=proc.stdout.readline().strip()
            if marker!='READY':
                out,err=proc.communicate(timeout=2)
                self.fail(f'fixture startup failed: {marker!r} {err}')
            proc.send_signal(signal.SIGTERM)
            out,err=proc.communicate(timeout=4)
            self.assertEqual(proc.returncode,0,err)
            self.assertIn(f'DRAINED 0 {count}',out)
        finally:
            if proc.poll() is None:
                # Only our disposable synthetic process group.
                os.killpg(proc.pid,signal.SIGKILL)
                proc.communicate(timeout=2)
            proc.stdout.close();proc.stderr.close()
