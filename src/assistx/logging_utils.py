from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, Response

from .tracing_utils import set_trace_context

LOG_FORMAT = os.getenv("LOG_FORMAT", "text")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = ""
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        cid = getattr(record, "correlation_id", None)
        if cid:
            obj["correlation_id"] = cid
        if record.exc_info and record.exc_info[0]:
            obj["exception"] = self.formatException(record.exc_info)
        for key in ("answer_id", "run_id", "job_id", "model", "mode", "status", "path", "method"):
            val = getattr(record, key, None)
            if val is not None:
                obj[key] = val
        return json.dumps(obj, default=str)


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(name or "assistx")


def setup_logging() -> None:
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(CorrelationIdFilter())
    if LOG_FORMAT == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
    root.addHandler(handler)


def install_logging_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _correlation_id_middleware(request: Request, call_next: Callable) -> Response:
        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        trace_id = request.headers.get("X-Trace-ID") or cid
        request.state.correlation_id = cid
        request.state.trace_id = trace_id
        set_trace_context(request)
        start = time.time()
        response: Response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Trace-ID"] = trace_id
        elapsed = time.time() - start
        logging.getLogger("access").info(
            "%s %s -> %d (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed * 1000,
            extra={"correlation_id": cid, "path": request.url.path, "method": request.method},
        )
        return response
