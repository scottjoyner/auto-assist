from __future__ import annotations
from typing import Any, Dict

from .llm_client import chat as _chat, tool_json as _tool_json
from .config import settings
from .cache import cache_get, cache_set, make_key
from .logging_utils import get_logger
from .metrics import LLM_TOKENS
from tenacity import retry, stop_after_attempt, wait_exponential

logger = get_logger()


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


SYSTEM_BASE = "You are a precise engineering assistant. Always return JSON strictly when asked."


class LLMError(Exception):
    pass


def _cache_or_call(key: str, call_fn):
    cached = cache_get(key)
    if cached is not None:
        logger.info(f"cache hit: {key[:24]}...")
        return cached
    out = call_fn()
    cache_set(key, out)
    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.8, min=0.5, max=4), reraise=True)
def json_chat(prompt: str, schema_hint: str | None = None, temperature: float = 0.2) -> Dict[str, Any]:
    key = make_key(settings.ollama_model, f"JSON|{schema_hint}|{prompt}", mode="json")

    def _do():
        messages = [
            {"role": "system", "content": SYSTEM_BASE + ("\nSchema:" + schema_hint if schema_hint else "")},
            {"role": "user", "content": prompt},
        ]
        raw = _chat(messages, model=settings.ollama_model, json_mode=True)
        return raw

    raw = _cache_or_call(key, _do)
    try:
        import orjson
        obj = orjson.loads(raw)
        LLM_TOKENS.labels(model=settings.ollama_model, mode="json").inc(_estimate_tokens(prompt))
        return obj
    except Exception as e:
        logger.warning("JSON parse failed; retrying...")
        raise LLMError("invalid json") from e


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.8, min=0.5, max=4), reraise=True)
def text_chat(prompt: str, temperature: float = 0.2) -> str:
    key = make_key(settings.ollama_model, f"TEXT|{prompt}", mode="text")

    def _do():
        messages = [
            {"role": "system", "content": SYSTEM_BASE},
            {"role": "user", "content": prompt},
        ]
        return _chat(messages, model=settings.ollama_model).strip()

    out = _cache_or_call(key, _do)
    LLM_TOKENS.labels(model=settings.ollama_model, mode="text").inc(_estimate_tokens(prompt))
    return out
