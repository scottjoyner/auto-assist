"""Unified LLM client facade (W-23).

Consolidation point for the previously duplicated LLM clients
(``llm/client.py`` (was ``llm_client.py``), ``agents/llm.py``, ``ollama_llm.py``,
``draft_model.py``). New code should call ``get_llm_client()`` and use the
returned client; the old module-level functions are re-exported for backward
compatibility but are slated for removal once callers migrate.

TODO(W-23): migrate ``agents/llm.py``, ``ollama_llm.py``, ``draft_model.py``
onto this facade so there is exactly one LLM client in the repo.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional

from .client import (  # noqa: F401 — backward-compatible re-exports
    chat,
    embed,
    tool_json,
    stream_chat,
    LLM_BACKEND,
    LLM_MODEL,
    OPENAI_BASE_URL,
    OPENAI_API_KEY,
    OLLAMA_HOST,
    EMBED_MODEL,
    _candidate_models,
)

logger = logging.getLogger(__name__)


class LLMClient:
    """Thin object facade over the module-level chat/embed functions.

    TODO(W-23): make this the single owner of HTTP sessions / circuit-breaker
    state so the duplicate clients can be deleted.
    """

    def __init__(self, model: Optional[str] = None, backend: Optional[str] = None):
        self.model = model or LLM_MODEL
        self.backend = backend or LLM_BACKEND

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        json_mode: bool = False,
    ) -> str:
        return chat(messages, model=model or self.model, json_mode=json_mode)

    def tool_json(self, messages: List[Dict[str, str]], model: Optional[str] = None) -> Dict[str, Any]:
        return tool_json(messages)

    def embed(self, text: str) -> Optional[List[float]]:
        return embed(text)

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        return stream_chat(messages, model=model or self.model, **kwargs)


_FACADE: Optional[LLMClient] = None


def get_llm_client(model: Optional[str] = None, backend: Optional[str] = None) -> LLMClient:
    """Return a shared ``LLMClient`` facade (lazy singleton).

    Centralizes LLM access so the duplicate clients (agents/llm.py,
    ollama_llm.py, draft_model.py) can later be deleted.
    """
    global _FACADE
    if _FACADE is None:
        _FACADE = LLMClient(model=model, backend=backend)
    return _FACADE


__all__ = ["LLMClient", "get_llm_client", "chat", "embed", "tool_json", "stream_chat"]
