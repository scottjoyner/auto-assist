"""Cooperative multiprocess supervisor. Never force-kill a running job."""
from contextlib import contextmanager
import signal
import time

@contextmanager
def shutdown_flag():
    requested=False
    previous={}
    def request(signum,frame):
        nonlocal requested
        requested=True
    try:
        for signum in (signal.SIGTERM,signal.SIGINT):
            previous[signum]=signal.signal(signum,request)
        yield lambda:requested
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)

def _child_entry(target,args,previous_mask):
    """Reset inherited parent handlers before unblocking termination signals."""
    signal.signal(signal.SIGTERM,signal.SIG_DFL)
    signal.signal(signal.SIGINT,signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_SETMASK,previous_mask)
    target(*args)


def launch_children(ctx,count,target,args_for_index,stopping):
    """Return owned children even when launch is interrupted or partially fails.

    Linux fork only. Signals are blocked across each fork, not across the
    whole batch; the parent observes a pending stop before the next child.
    The child resets handlers before entering its normal runtime startup.
    A returned error is a startup fault, never permission to open admission.
    """
    processes=[]
    for i in range(count):
        if stopping():
            break
        p=None
        old_mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGTERM,signal.SIGINT})
        try:
            p=ctx.Process(target=_child_entry,
                          args=(target,args_for_index(i),old_mask),daemon=False)
            p.start()
            processes.append(p)
        except Exception as exc:
            if p is not None and p.pid is not None and p not in processes:
                processes.append(p)
            return processes,type(exc).__name__
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK,old_mask)
    return processes,None


def await_start_gate(gate,stopping,*,timeout=90,poll_seconds=.05):
    """Bounded local startup barrier; shutdown wins over opening admission."""
    deadline=time.monotonic()+timeout
    while not stopping():
        if gate.is_set():
            return not stopping()
        if time.monotonic()>=deadline:
            return False
        time.sleep(poll_seconds)
    return False


def open_group_admission(gate,processes,expected,stopping):
    """Parent-only release, after the caller verifies every readiness ack."""
    if expected<1 or len(processes)!=expected or stopping():
        return False
    if any(not p.is_alive() for p in processes) or stopping():
        return False
    gate.set()
    return True


def readiness_worker_class(base,ready,*,startup_gate=None,stop_exception=None,gate_timeout=90):
    """Acknowledge real RQ handlers, then fence the first queue check.

    RQ 1.16.2 calls check_for_suspension inside its work-loop try/finally,
    before any dequeue. A closed or timed-out gate raises its StopRequested
    so ordinary teardown runs; no synthetic broker operation is needed.
    """
    if startup_gate is not None and stop_exception is None:
        raise ValueError("startup gate requires the runtime stop exception")
    class ReadyWorker(base):
        def _install_signal_handlers(self):
            super()._install_signal_handlers()
            try:
                ready.send_bytes(b"RQ_READY_V1")
            finally:
                ready.close()

        def check_for_suspension(self,burst):
            if startup_gate is not None and not await_start_gate(
                startup_gate,lambda:bool(self._stop_requested),timeout=gate_timeout,
            ):
                raise stop_exception()
            return super().check_for_suspension(burst)
    return ReadyWorker


def wait_child_readiness(processes,readers,stopping,*,timeout=60,poll_seconds=.05):
    """One bounded readiness window for the whole group, never per-child."""
    if len(processes)!=len(readers) or not processes:
        return "readiness_incomplete"
    pending=set(range(len(processes)))
    deadline=time.monotonic()+timeout
    while True:
        if stopping():
            return "shutdown_requested"
        if any(not p.is_alive() for p in processes):
            return "child_exited"
        for i in tuple(pending):
            try:
                if readers[i].poll(0):
                    if readers[i].recv_bytes(32)!=b"RQ_READY_V1":
                        return "readiness_invalid"
                    pending.remove(i)
            except (OSError,EOFError):
                return "readiness_channel_failed"
        if not pending:
            return "shutdown_requested" if stopping() else (
                "child_exited" if any(not p.is_alive() for p in processes) else None)
        if time.monotonic()>=deadline:
            return "readiness_timeout"
        time.sleep(poll_seconds)


def supervise(processes,stopping,*,grace_seconds=120,poll_seconds=.1,on_shutdown=None,
              fail_on_child_exit=False,faulted=None):
    """Forward one warm shutdown signal; report timeout without SIGKILL.

    A child that stays busy can keep the parent alive through Python's
    multiprocessing shutdown machinery. Deployment must allow a longer
    container stop grace than the intended application drain window.
    """
    deadline=None
    failure=0
    while True:
        alive=[p for p in processes if p.is_alive()]
        if deadline is None:
            requested=stopping()
            unexpected=not requested and (
                (fail_on_child_exit and len(alive)!=len(processes))
                or (faulted is not None and faulted())
            )
            if requested or unexpected:
                if unexpected:
                    failure=next((p.exitcode for p in processes if p.exitcode not in (0,None)),70)
                deadline=time.monotonic()+grace_seconds
                if on_shutdown is not None:on_shutdown()
                for p in alive:
                    try:p.terminate()
                    except ProcessLookupError:pass
        if not alive:
            return failure or next((p.exitcode for p in processes if p.exitcode not in (0,None)),0)
        if deadline is not None and time.monotonic()>=deadline:return 75
        time.sleep(poll_seconds)
        for p in processes:
            if not p.is_alive():p.join(timeout=0)
