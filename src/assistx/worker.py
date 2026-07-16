# src/assistx/worker.py
import json
import logging
import multiprocessing as mp
import os
import socket
import threading
import time

from .config import settings
from .deps import load_redis_module, use_compat_shims
from .runtime import validate_runtime_configuration

logger = logging.getLogger(__name__)

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


def _start_execution_pollers() -> list[threading.Thread]:
    """W-22: start execution-authority pollers behind a single config switch.

    ``EXECUTION_BACKEND`` (paperclip|direct|auto) decides which execution
    authority runs. Both implementations are preserved; neither is removed.
    """
    threads: list[threading.Thread] = []
    backend = settings.execution_backend

    want_paperclip = backend in ("paperclip", "auto")
    want_direct = backend in ("direct", "auto")

    if want_paperclip:
        try:
            from .paperclip_poller import paperclip_poll_job

            def _paperclip_loop() -> None:
                while True:
                    try:
                        paperclip_poll_job()
                    except Exception as exc:  # pragma: no cover
                        logger.warning("paperclip poller error: %s", exc)
                    time.sleep(int(os.getenv("PAPERCLIP_POLL_INTERVAL", "30")))

            t = threading.Thread(target=_paperclip_loop, name="paperclip-poller", daemon=True)
            t.start()
            threads.append(t)
            logger.info("execution backend=%s: paperclip poller started", backend)
        except Exception as exc:  # pragma: no cover
            logger.warning("paperclip poller unavailable: %s", exc)

    if want_direct:
        try:
            from .agents.hermes_agent_adapter import run_loop as hermes_run_loop

            t = threading.Thread(target=hermes_run_loop, name="hermes-adapter-poller", daemon=True)
            t.start()
            threads.append(t)
            logger.info("execution backend=%s: direct hermes_agent_adapter poller started", backend)
        except Exception as exc:  # pragma: no cover
            logger.warning("hermes_agent_adapter poller unavailable: %s", exc)

    if not threads:
        logger.warning("no execution pollers started for backend=%s", backend)
    logger.info(
        "execution authority active backend=%s (paperclip=%s, direct=%s) pollers=%d",
        backend,
        want_paperclip,
        want_direct,
        len(threads),
    )
    return threads


def main():
    validate_runtime_configuration(strict=True)
    _start_execution_pollers()
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
