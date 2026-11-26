#!/usr/bin/env python3
"""Synthetic idle/busy RQ signal handling in the existing container interpreter.

Uses real SimpleWorker signal handlers but no broker, jobs, credentials,
network requests, live process signals or live source changes.
"""
import base64, hashlib, json, pathlib, subprocess, sys

ROOT=pathlib.Path(__file__).resolve().parents[1]
REMOTE=r"""
import base64, hashlib, json, logging, multiprocessing as mp, os, signal, sys, threading, time, types
from rq import SimpleWorker, __version__
from rq.worker import WorkerStatus, StopRequested
source=base64.b64decode(sys.stdin.buffer.read(65537),validate=True)
if hashlib.sha256(source).hexdigest()!=sys.argv[1]:raise SystemExit('source pin mismatch')
m=types.ModuleType('candidate_supervision')
exec(compile(source,'candidate_supervision.py','exec'),m.__dict__)
ctx=mp.get_context('fork')
def child(busy, ready, result):
    cls=m.readiness_worker_class(SimpleWorker,ready)
    w=cls.__new__(cls)
    w._state=WorkerStatus.BUSY if busy else WorkerStatus.IDLE
    w.name='synthetic-rq-worker';w.pid=os.getpid()
    w.log=logging.getLogger('synthetic-rq-worker')
    w.scheduler=None;w._stop_requested=False
    w.set_shutdown_requested_date=lambda:None
    w._install_signal_handlers()
    try:
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if busy and w._stop_requested:
                time.sleep(.1)
                result.send({'kind':'busy','warmStopObserved':True,'syntheticJobFinished':True})
                return
            time.sleep(.01)
    except StopRequested:
        result.send({'kind':'idle','warmStopObserved':True,'syntheticJobFinished':False})
        return
    raise SystemExit(4)
rows=[];children=[];readers=[];events=[]
for busy in (False,True):
    ready_reader,ready=ctx.Pipe(duplex=False)
    recv,send=ctx.Pipe(duplex=False)
    p=ctx.Process(target=child,args=(busy,ready,send))
    p.start();send.close();ready.close();children.append(p);readers.append(recv);events.append(ready_reader)
try:
    if m.wait_child_readiness(children,events,lambda:False,timeout=2,poll_seconds=.01) is not None:raise RuntimeError('fixture not ready')
    with m.shutdown_flag() as stopping:
        timer=threading.Timer(.05,lambda:os.kill(os.getpid(),signal.SIGTERM))
        timer.start()
        try:rc=m.supervise(children,stopping,grace_seconds=2,poll_seconds=.01)
        finally:timer.cancel();timer.join()
    if rc!=0 or any(p.exitcode!=0 for p in children):raise RuntimeError('fixture did not drain')
    for r in readers:
        if not r.poll(.5):raise RuntimeError('missing child result')
        rows.append(r.recv())
    if rows!=[{'kind':'idle','warmStopObserved':True,'syntheticJobFinished':False},{'kind':'busy','warmStopObserved':True,'syntheticJobFinished':True}]:raise RuntimeError('wrong result')
    print(json.dumps({'schema':'assistx-rq-shutdown-rehearsal-v1','rqVersion':__version__,'candidateSha256':sys.argv[1],'supervisorExit':rc,'readyAcknowledgements':len(events),'children':rows,'liveSignalsSent':0,'brokerRequests':0,'productionCutoverPerformed':False},sort_keys=True))
finally:
    # Only disposable child processes from this fixture.
    for p in children:
        if p.is_alive():p.kill()
        p.join(1)
    for r in readers+events:r.close()
"""
def main():
    source=(ROOT/'src/assistx/worker_supervision.py').read_bytes()
    digest=hashlib.sha256(source).hexdigest()
    result=subprocess.run(['docker','exec','-i','assistx-worker','python','-E','-B','-c',REMOTE,digest],input=base64.b64encode(source),capture_output=True,timeout=10,check=True)
    row=json.loads(result.stdout)
    if row.get('candidateSha256')!=digest or row.get('supervisorExit')!=0 or row.get('brokerRequests')!=0 or row.get('liveSignalsSent')!=0 or row.get('readyAcknowledgements')!=2:raise SystemExit('receipt mismatch')
    print(json.dumps(row,sort_keys=True))
if __name__=='__main__':main()
