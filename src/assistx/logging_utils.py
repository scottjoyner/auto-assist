import os
from loguru import logger
import sys

LOG_FORMAT = os.getenv("LOG_FORMAT", "text")  # "text" or "json"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOGGING_EXTRA = ["answer_id", "run_id", "job_id", "model", "mode", "status", "path", "method"]

logger.remove()
if LOG_FORMAT == "json":
    logger.add(sys.stdout, level=LOG_LEVEL, serialize=True, backtrace=False, diagnose=False)
else:
    logger.add(sys.stdout, level=LOG_LEVEL, serialize=False, backtrace=False, diagnose=False,
               format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

def get_logger():
    return logger
