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

def supervise(processes,stopping,*,grace_seconds=120,poll_seconds=.1):
    """Forward one warm shutdown signal; report timeout without SIGKILL.

    A child that stays busy can keep the parent alive through Python's
    multiprocessing shutdown machinery. Deployment must allow a longer
    container stop grace than the intended application drain window.
    """
    deadline=None
    while True:
        alive=[p for p in processes if p.is_alive()]
        if not alive:
            return next((p.exitcode for p in processes if p.exitcode not in (0,None)),0)
        if stopping() and deadline is None:
            deadline=time.monotonic()+grace_seconds
            for p in alive:
                try:p.terminate()
                except ProcessLookupError:pass
        if deadline is not None and time.monotonic()>=deadline:return 75
        time.sleep(poll_seconds)
        for p in processes:
            if not p.is_alive():p.join(timeout=0)
