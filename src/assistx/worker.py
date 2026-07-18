# src/assistx/worker.py
import faulthandler
import json
import logging
import multiprocessing as mp
import os
import signal
import socket
import threading
import time

# Dump all thread stacks on SIGUSR1 for debugging wedged workers.
faulthandler.enable()
try:
    import sys
    def _dump_threads(*_):
        with open("/tmp/faulthandler_dump.txt", "w") as _fh:
            faulthandler.dump_traceback(all_threads=True, file=_fh)
    signal.signal(signal.SIGUSR1, _dump_threads)
except (ValueError, OSError):
    pass

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
    from rq import SimpleWorker

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
    # Agent loops (decide + tool calls + retries) can run well past RQ's
    # default 180s. We raise the default timeout on the Queue instance so jobs
    # we pull use a generous window instead of being killed mid-loop.
    job_timeout = int(os.getenv("RQ_JOB_TIMEOUT_S", "1800"))
    # The heavy import chain (assistx.jobs -> langgraph -> langchain -> tornado)
    # was already warmed up in the parent process and inherited via fork, so it
    # is cached in sys.modules. This no-op import never touches the import lock
    # and guards against any module that slipped the parent warmup.
    try:
        import assistx.jobs  # noqa: F401
        from .agents.orchestrator import run_task  # noqa: F401
    except Exception as exc:  # pragma: no cover
        logger.warning("child warmup import failed: %s", exc)
    with Connection(conn):
        queues = [Queue(name, default_timeout=job_timeout) for name in listen]
        # Use SimpleWorker: runs jobs in-process without forking a horse.
        # The default Worker forks, and forking after background threads were
        # started deadlocks the child on Python's import lock (classic
        # fork-after-thread). SimpleWorker reuses the already-imported modules
        # in the worker process, eliminating that deadlock.
        # with_scheduler=False: RQ's scheduler spawns a background thread that
        # imports concurrently and races on the import lock; we don't need the
        # scheduler (the drainer enqueues directly).
        w = SimpleWorker(queues, name=worker_name)
        w.work(with_scheduler=False)


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
    # Warm up the full import chain in the PARENT process, single-threaded,
    # BEFORE forking the worker children. The fork copy-on-write then hands
    # each child an already-populated sys.modules, so child job execution
    # never has to import (assistx.jobs -> langgraph -> langchain -> tornado,
    # a huge chain). This avoids Python's per-process import-lock deadlock that
    # occurs when a worker thread (e.g. RQ's scheduler) imports the same chain
    # concurrently with a job thread.
    try:
        import importlib
        _warm = [
            "assistx.jobs",
            "assistx.agents.orchestrator",
            "assistx.ollama_llm",
            "assistx.metrics",
            "assistx.tools.web_search",
            "assistx.llm.client",
            "assistx.fleet",
            "assistx.outbox_client",
            "assistx.neo4j_client",
            "prometheus_client",
            "prometheus_client.exposition",
            "prometheus_client.registry",
            "prometheus_client.openmetrics",
            "langgraph",
            "langchain_core",
            "tornado",
            "tenacity",
        ]
        for _m in _warm:
            try:
                importlib.import_module(_m)
            except Exception as exc:  # pragma: no cover
                logger.warning("parent warmup import of %s failed: %s", _m, exc)
    except Exception as exc:  # pragma: no cover
        logger.warning("parent warmup failed: %s", exc)
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
