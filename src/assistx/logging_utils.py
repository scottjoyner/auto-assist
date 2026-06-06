import os
import sys
import logging

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover - fallback for stripped test envs
    logger = logging.getLogger("assistx")

LOG_FORMAT = os.getenv("LOG_FORMAT", "text")  # "text" or "json"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOGGING_EXTRA = ["answer_id", "run_id", "job_id", "model", "mode", "status", "path", "method"]

if hasattr(logger, "remove"):
    logger.remove()
    if LOG_FORMAT == "json":
        logger.add(sys.stdout, level=LOG_LEVEL, serialize=True, backtrace=False, diagnose=False)
    else:
        logger.add(sys.stdout, level=LOG_LEVEL, serialize=False, backtrace=False, diagnose=False,
                   format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
else:
    logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper(), logging.INFO), stream=sys.stdout)


def get_logger():
    return logger
