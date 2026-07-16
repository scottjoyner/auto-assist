"""Backward-compatibility shim for the relocated LLM client (W-23).

The canonical client now lives at ``assistx.llm.client`` and is exposed via
``assistx.llm``. This module re-exports it so existing
``from .llm_client import ...`` imports keep working until callers migrate to
``from .llm import get_llm_client`` / ``from .llm import chat``.

TODO(W-23): delete this shim once all callers are migrated.
"""

from .llm.client import (  # noqa: F401
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
)
