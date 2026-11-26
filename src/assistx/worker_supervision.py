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
