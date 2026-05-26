import json
from typing import Any, Dict, List, Optional

from ..llm_client import chat as _chat, tool_json as _tool_json


def chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    json_mode: bool = False,
) -> str:
    return _chat(messages, model=model, json_mode=json_mode)


def tool_json(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    return _tool_json(messages)
