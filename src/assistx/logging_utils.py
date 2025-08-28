
from __future__ import annotations
from loguru import logger
import sys

logger.remove()
logger.add(sys.stdout, level="INFO", serialize=False, backtrace=False, diagnose=False,
           format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

def get_logger():
    return logger
