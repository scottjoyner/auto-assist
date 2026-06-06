# src/assistx/worker.py
import multiprocessing as mp
import os

from .deps import load_redis_module, use_compat_shims
from .runtime import validate_runtime_configuration

redis = load_redis_module()
if use_compat_shims():
    try:
        from rq import Worker, Queue, Connection
    except ModuleNotFoundError:
        from .compat import InMemoryQueue as Queue
        Worker = Connection = None
else:
    from rq import Worker, Queue, Connection

def _run_one_worker(index: int, listen: list[str], redis_url: str) -> None:
    conn = redis.from_url(redis_url)
    worker_name = f"assistx-worker-{index}"
    with Connection(conn):
        w = Worker([Queue(name) for name in listen], name=worker_name)
        # enable scheduler so delayed/retry jobs work if present
        w.work(with_scheduler=True)


def main():
    validate_runtime_configuration(strict=True)
    listen = [os.getenv("RQ_QUEUE", "assistx")]
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    concurrency = max(1, int(os.getenv("WORKER_CONCURRENCY", "1")))

    if concurrency == 1:
        _run_one_worker(1, listen, redis_url)
        return

    processes: list[mp.Process] = []
    for i in range(concurrency):
        p = mp.Process(target=_run_one_worker, args=(i + 1, listen, redis_url), daemon=False)
        p.start()
        processes.append(p)

    exit_code = 0
    for p in processes:
        p.join()
        if p.exitcode not in (0, None):
            exit_code = p.exitcode
    if exit_code:
        raise SystemExit(exit_code)

if __name__ == "__main__":
    main()
