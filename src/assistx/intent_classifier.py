from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CLASSIFICATION_MEMORY = "memory"
CLASSIFICATION_TASK = "task"
CLASSIFICATION_CANCEL = "cancel"
CLASSIFICATION_QUERY = "query"
CLASSIFICATION_UNKNOWN = "unknown"

CANCEL_PATTERNS: List[str] = [
    r"\bcancel\b", r"\bstop\b", r"\bnever mind\b", r"\bforget it\b",
    r"\bignore that\b", r"\bdon't\b.*\bdo that\b", r"\bscratch that\b",
    r"\bdisregard\b",
]

TASK_PATTERNS: List[str] = [
    r"\b(please\s+)?(can you|could you|will you|would you|i need you to|i want you to|your task is|please)\b",
    r"\bmake\b", r"\bcreate\b", r"\bbuild\b", r"\bwrite\b", r"\bgenerate\b",
    r"\bfind\b", r"\bsearch\b", r"\blook up\b", r"\bresearch\b",
    r"\bsend\b", r"\bemail\b", r"\bmessage\b",
    r"\bcheck\b", r"\bverify\b", r"\bvalidate\b",
    r"\bupdate\b", r"\bmodify\b", r"\bchange\b", r"\bfix\b",
    r"\bschedule\b", r"\bremind\b", r"\bset up\b",
]

QUERY_PATTERNS: List[str] = [
    r"^(what|who|where|when|why|how)\b",
    r"\?$",
]

MEMORY_PATTERNS: List[str] = [
    r"\bremember\b", r"\bnote\b", r"\bi think\b", r"\bi feel\b", r"\bi like\b",
    r"\bi prefer\b", r"\bmy favorite\b", r"\bremind me\b",
    r"\bkeep in mind\b", r"\bfor the record\b", r"\bjust so you know\b",
    r"\binterest\b", r"\bhobby\b", r"\bidea\b",
]


def classify_text(text: str) -> str:
    if not text or not text.strip():
        return CLASSIFICATION_UNKNOWN

    text_lower = text.strip().lower()

    for pattern in CANCEL_PATTERNS:
        if re.search(pattern, text_lower):
            return CLASSIFICATION_CANCEL

    for pattern in QUERY_PATTERNS:
        if re.search(pattern, text_lower):
            return CLASSIFICATION_QUERY

    for pattern in MEMORY_PATTERNS:
        if re.search(pattern, text_lower):
            return CLASSIFICATION_MEMORY

    for pattern in TASK_PATTERNS:
        if re.search(pattern, text_lower):
            return CLASSIFICATION_TASK

    short = len(text_lower.split()) <= 5
    if short:
        return CLASSIFICATION_MEMORY

    return CLASSIFICATION_TASK


def extract_title(text: str, max_words: int = 8) -> str:
    words = text.strip().split()
    if not words:
        return "Untitled"
    title = " ".join(words[:max_words])
    if len(words) > max_words:
        title += "..."
    return title[:120]
