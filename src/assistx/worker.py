# src/assistx/worker.py
import json
import multiprocessing as mp
import os
import socket
import threading
import time

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

HEALTH_PORT = int(os.getenv("WORKER_HEALTH_PORT", "8100"))


def _worker_name(index: int) -> str:
    hostname = socket.gethostname().split(".", 1)[0].replace("_", "-")
    pid = os.getpid()
    return f"assistx-worker-{index}-{hostname}-{pid}"


def _health_server() -> None:
    """Minimal HTTP health endpoint so Docker can check worker liveness."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", HEALTH_PORT))
    server.listen(1)
    server.settimeout(1.0)
    resp = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: 41\r\n"
        "Connection: close\r\n"
        "\r\n"
        '{"ok":true,"service":"auto-assist-worker"}\r\n'
    )
    while True:
        try:
            conn, _ = server.accept()
            conn.recv(1024)
            conn.sendall(resp.encode())
            conn.close()
        except socket.timeout:
            continue
        except Exception:
            break


def _run_one_worker(index: int, listen: list[str], redis_url: str) -> None:
    conn = redis.from_url(redis_url)
    worker_name = _worker_name(index)
    with Connection(conn):
        w = Worker([Queue(name) for name in listen], name=worker_name)
        w.work(with_scheduler=True)


def main():
    validate_runtime_configuration(strict=True)
    listen = [os.getenv("RQ_QUEUE", "assistx")]
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    concurrency = max(1, int(os.getenv("WORKER_CONCURRENCY", "1")))

    t = threading.Thread(target=_health_server, daemon=True)
    t.start()

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
