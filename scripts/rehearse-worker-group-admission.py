#!/usr/bin/env python3
"""Real installed RQ signal handlers + group gate, with no broker access."""
import base64,hashlib,json,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
REMOTE=r"""
import base64,hashlib,json,logging,multiprocessing as mp,os,signal,sys,time,types
from rq import SimpleWorker,__version__
from rq.worker import StopRequested,WorkerStatus
source=base64.b64decode(sys.stdin.buffer.read(65537),validate=True)
assert hashlib.sha256(source).hexdigest()==sys.argv[1]
m=types.ModuleType('candidate_supervision');exec(compile(source,'candidate_supervision.py','exec'),m.__dict__)
ctx=mp.get_context('fork')
def child(ready, gate, result):
    class WitnessRQ(SimpleWorker):
        def check_for_suspension(self,burst):
            result.send_bytes(b'QUEUE_CHECK')
    cls=m.readiness_worker_class(WitnessRQ,ready,startup_gate=gate,stop_exception=StopRequested,gate_timeout=3)
    w=cls.__new__(cls)
    w._state=WorkerStatus.IDLE
    w.name='synthetic-group-worker';w.pid=os.getpid()
    w.log=logging.getLogger('synthetic-group-worker');w.scheduler=None;w._stop_requested=False
    w.set_shutdown_requested_date=lambda:None
    w._install_signal_handlers()
    try:
        w.check_for_suspension(False)
    except StopRequested:
        result.send_bytes(b'WARM_STOP')
    finally:
        result.close()
def run(release):
    gate=ctx.Event();children=[];acks=[];results=[]
    try:
        for i in range(2):
            a,b=ctx.Pipe(duplex=False);r,s=ctx.Pipe(duplex=False)
            p=ctx.Process(target=child,args=(b,gate,s));p.start()
            b.close();s.close();children.append(p);acks.append(a);results.append(r)
        assert m.wait_child_readiness(children,acks,lambda:False,timeout=2,poll_seconds=.005) is None
        assert all(not r.poll(.1) for r in results),'queue check before group release'
        if release:
            assert m.open_group_admission(gate,children,2,lambda:False)
        else:
            for p in children:p.terminate()
        expected=b'QUEUE_CHECK' if release else b'WARM_STOP'
        for r in results:
            assert r.poll(2) and r.recv_bytes(32)==expected
        for p in children:p.join(1);assert p.exitcode==0
        return {'readyAcks':2,'queueChecksBeforeRelease':0,'queueChecksAfterRelease':2 if release else 0,'warmStopsBeforeRelease':0 if release else 2}
    finally:
        # Cleanup only fixture-owned processes.
        for p in children:
            if p.is_alive():p.kill()
            p.join(1)
        for r in acks+results:r.close()
print(json.dumps({'schema':'assistx-rq-group-admission-rehearsal-v1','rqVersion':__version__,'candidateSha256':sys.argv[1],'normalRelease':run(True),'shutdownBeforeRelease':run(False),'brokerRequests':0,'liveSignalsSent':0,'productionCutoverPerformed':False},sort_keys=True))
"""
def main():
    source=(ROOT/'src/assistx/worker_supervision.py').read_bytes()
    digest=hashlib.sha256(source).hexdigest()
    p=subprocess.run(['docker','exec','-i','assistx-worker','python','-E','-B','-c',REMOTE,digest],input=base64.b64encode(source),capture_output=True,timeout=12,check=True)
    row=json.loads(p.stdout)
    if row.get('candidateSha256')!=digest or row.get('brokerRequests')!=0 or row.get('liveSignalsSent')!=0:raise SystemExit('receipt mismatch')
    if row['normalRelease']!={'readyAcks':2,'queueChecksBeforeRelease':0,'queueChecksAfterRelease':2,'warmStopsBeforeRelease':0}:raise SystemExit('release mismatch')
    if row['shutdownBeforeRelease']!={'readyAcks':2,'queueChecksBeforeRelease':0,'queueChecksAfterRelease':0,'warmStopsBeforeRelease':2}:raise SystemExit('stop mismatch')
    print(json.dumps(row,sort_keys=True))
if __name__=='__main__':main()
