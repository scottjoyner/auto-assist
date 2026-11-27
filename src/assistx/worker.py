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
        from rq import Connection, Queue, Worker
    except ModuleNotFoundError:
        from .compat import InMemoryQueue as Queue

        Worker = Connection = None
else:
    from rq import Connection, Queue, SimpleWorker, Worker

HEALTH_PORT = int(os.getenv("WORKER_HEALTH_PORT", "8100"))


def _worker_name(index: int) -> str:
    hostname = socket.gethostname().split(".", 1)[0].replace("_", "-")
    pid = os.getpid()
    return f"assistx-worker-{index}-{hostname}-{pid}"


def _health_response(draining: bool) -> bytes:
    body = json.dumps({"ok": not draining, "service": "auto-assist-worker"},
                      separators=(",", ":")).encode("utf-8")
    status = "503 Service Unavailable" if draining else "200 OK"
    return (f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode("ascii") + body


def _health_unavailable(stop_event, startup_gate=None):
    return stop_event.is_set() or (startup_gate is not None and not startup_gate.is_set())


def _wait_health_listener(ready, thread, stopping, *, timeout=5):
    deadline = time.monotonic() + timeout
    while not stopping() and thread.is_alive():
        if ready.wait(.05):
            return not stopping() and thread.is_alive()
        if time.monotonic() >= deadline:
            break
    return False


def _health_server(stop_event: threading.Event, startup_gate=None, listening=None) -> None:
    """Report supervised liveness, not broker readiness or claim health."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", HEALTH_PORT))
        server.listen(8)
        server.settimeout(1.0)
        if listening is not None:
            listening.set()
        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(1.0)
                    conn.recv(1024)
                    conn.sendall(_health_response(_health_unavailable(stop_event, startup_gate)))
                except OSError:
                    continue


def _run_one_worker(index: int, listen: list[str], redis_url: str, ready, startup_gate) -> None:
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
        from .worker_supervision import readiness_worker_class
        from rq.worker import StopRequested
        w = readiness_worker_class(
            SimpleWorker, ready, startup_gate=startup_gate,
            stop_exception=StopRequested,
        )(queues, name=worker_name)
        w.work(with_scheduler=False)


def _start_execution_pollers(stop_event: threading.Event, *, startup_gate=None) -> list[threading.Thread]:
    """W-22: start execution-authority pollers behind a single config switch.

    ``EXECUTION_BACKEND`` (paperclip|direct|auto) decides which execution
    authority runs. Both implementations are preserved; neither is removed.
    """
    from .worker_supervision import await_start_gate

    def admitted():
        if startup_gate is None:
            return not stop_event.is_set()
        return await_start_gate(startup_gate, stop_event.is_set)

    threads: list[threading.Thread] = []
    backend = settings.execution_backend

    want_paperclip = backend in ("paperclip", "auto")
    want_direct = backend in ("direct", "auto")

    if want_paperclip:
        try:
            from .paperclip_poller import paperclip_poll_job

            def _paperclip_loop() -> None:
                if not admitted():
                    return
                while not stop_event.is_set():
                    try:
                        paperclip_poll_job()
                    except Exception as exc:  # pragma: no cover
                        logger.warning("paperclip poller error: %s", exc)
                    stop_event.wait(int(os.getenv("PAPERCLIP_POLL_INTERVAL", "30")))

            t = threading.Thread(
                target=_paperclip_loop,
                name="paperclip-poller",
                daemon=False,
            )
            t.start()
            threads.append(t)
            logger.info("execution backend=%s: paperclip poller started", backend)
        except Exception as exc:  # pragma: no cover
            logger.warning("paperclip poller unavailable: %s", exc)

    if want_direct:
        try:
            from .agents import hermes_agent_adapter
            from .strict_executor_adapter import install_strict_executor_adapter

            install_strict_executor_adapter(hermes_agent_adapter)
            hermes_run_loop = hermes_agent_adapter.run_loop

            def _direct_loop():
                if admitted() and not stop_event.is_set():
                    hermes_run_loop(stopping=stop_event.is_set)

            t = threading.Thread(
                target=_direct_loop,
                name="hermes-adapter-poller",
                daemon=False,
            )
            t.start()
            threads.append(t)
            logger.info(
                "execution backend=%s: direct hermes_agent_adapter poller started",
                backend,
            )
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


def _recovery_required():
    return os.getenv("ASSISTX_RECOVERY_ISLAND_DISPATCH_ENABLED", "false").lower() in {
        "1", "true", "yes", "on"
    }


def _start_recovery_island_controller(stop_event: threading.Event, *, startup_gate=None) -> threading.Thread | None:
    """Start the Neo4j-fenced Beelink dispatcher when explicitly enabled."""
    try:
        from .recovery_island_control import start_recovery_island_dispatcher

        thread = start_recovery_island_dispatcher(stop_event=stop_event, startup_gate=startup_gate)
        if thread is not None:
            logger.info("fenced recovery-island dispatcher started")
        return thread
    except Exception as exc:  # pragma: no cover
        logger.warning("recovery-island dispatcher unavailable: %s", exc)
        return None


def _warm_parent(stopping):
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
            "assistx.recovery_island_control",
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
            if stopping():
                return
            try:
                importlib.import_module(_m)
            except Exception as exc:  # pragma: no cover
                logger.warning("parent warmup import of %s failed: %s", _m, exc)
    except Exception as exc:  # pragma: no cover
        logger.warning("parent warmup failed: %s", exc)


def _run_worker_group(stopping):
    listen = [os.getenv("RQ_QUEUE", "assistx")]
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    concurrency = max(1, int(os.getenv("WORKER_CONCURRENCY", "1")))

    from .worker_supervision import launch_children, supervise, wait_child_readiness, open_group_admission

    # main() owns the signal handler from validation through final drain.
    ctx = mp.get_context("fork")
    startup_gate = ctx.Event()
    stop_event = threading.Event()
    background = []
    processes = []
    channels = []
    startup_error = None
    t = None
    try:
        try:
            for _ in range(concurrency):
                if stopping():
                    break
                channels.append(ctx.Pipe(duplex=False))
            if not stopping():
                processes, startup_error = launch_children(
                    ctx, concurrency, _run_one_worker,
                    lambda i: (i + 1, listen, redis_url, channels[i][1], startup_gate), stopping,
                )
                for reader, writer in channels:
                    writer.close()
                if not startup_error and not stopping():
                    startup_error = wait_child_readiness(
                        processes, [reader for reader, _ in channels], stopping,
                    )
                    if startup_error == "shutdown_requested":
                        startup_error = None
        except Exception as exc:
            startup_error = type(exc).__name__
        finally:
            for reader, writer in channels:
                for channel in (reader, writer):
                    try:
                        channel.close()
                    except Exception as exc:
                        startup_error = startup_error or type(exc).__name__
        if startup_error:
            logger.error("worker startup failed: %s", startup_error)
            stop_event.set()
        try:
            if not stopping() and not startup_error and len(processes) == concurrency:
                background.extend(_start_execution_pollers(stop_event, startup_gate=startup_gate))
                expected = 2 if settings.execution_backend == "auto" else 1
                if len(background) != expected:
                    startup_error = "execution_poller_startup_incomplete"
                    stop_event.set()
                if not startup_error and not stopping():
                    controller = _start_recovery_island_controller(stop_event, startup_gate=startup_gate)
                    if controller is not None:
                        background.append(controller)
                    elif _recovery_required():
                        startup_error = "recovery_dispatcher_startup_failed"
                        stop_event.set()
                if not startup_error and not stopping():
                    listening = threading.Event()
                    t = threading.Thread(
                        target=_health_server,
                        args=(stop_event, startup_gate, listening), daemon=True,
                    )
                    t.start()

                    def startup_stopping():
                        return stopping() or stop_event.is_set() or any(
                            not thread.is_alive() for thread in background
                        )

                    if (not _wait_health_listener(listening, t, startup_stopping)
                            or not open_group_admission(startup_gate, processes, concurrency, startup_stopping)):
                        startup_error = "group_admission_not_opened"
                        stop_event.set()
        except Exception as exc:
            startup_error = type(exc).__name__
            stop_event.set()
        exit_code = supervise(
            processes, stopping, on_shutdown=stop_event.set,
            fail_on_child_exit=True,
            faulted=lambda: bool(startup_error or len(processes) != concurrency
                                 or t is None or not t.is_alive()
                                 or any(not thread.is_alive() for thread in background)),
        )
        if startup_error and not exit_code:
            exit_code = 70
        return exit_code
    finally:
        stop_event.set()
        for thread in background:
            thread.join()


def main():
    from .worker_supervision import shutdown_flag

    with shutdown_flag() as stopping:
        validate_runtime_configuration(strict=True)
        if stopping():
            return
        _warm_parent(stopping)
        if stopping():
            return
        exit_code = _run_worker_group(stopping)

    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
