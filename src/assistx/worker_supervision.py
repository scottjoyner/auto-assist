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
        except (OSError,RuntimeError) as exc:
            if p is not None and p.pid is not None and p not in processes:
                processes.append(p)
            return processes,type(exc).__name__
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK,old_mask)
    return processes,None


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
