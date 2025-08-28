
from __future__ import annotations
from typing import Any, Dict
from ollama import Client
from .config import settings
import orjson
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from .cache import cache_get, cache_set, make_key
from .logging_utils import get_logger

logger = get_logger()
client = Client(host=settings.ollama_host)

SYSTEM_BASE = (
    "You are a precise engineering assistant. Always return JSON strictly when asked."
)

class LLMError(Exception): pass

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
        resp = client.chat(model=settings.ollama_model, messages=messages, options={"temperature": temperature, "num_ctx": 8192}, format="json")
        return resp["message"]["content"]
    raw = _cache_or_call(key, _do)
    try:
        return orjson.loads(raw)
    except Exception as e:
        logger.warning("JSON parse failed; retrying...")
        raise LLMError("invalid json" )

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.8, min=0.5, max=4), reraise=True)
def text_chat(prompt: str, temperature: float = 0.2) -> str:
    key = make_key(settings.ollama_model, f"TEXT|{prompt}", mode="text")
    def _do():
        messages = [
            {"role": "system", "content": SYSTEM_BASE},
            {"role": "user", "content": prompt},
        ]
        resp = client.chat(model=settings.ollama_model, messages=messages, options={"temperature": temperature, "num_ctx": 8192})
        return resp["message"]["content"].strip()
    return _cache_or_call(key, _do)
