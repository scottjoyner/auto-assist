import os, json, time, requests
from typing import Optional, Dict, Any, List

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
TIMEOUT = int(os.getenv("LLM_TIMEOUT_S", "180"))

def chat(messages: List[Dict[str, str]], model: Optional[str] = None, json_mode: bool = False) -> str:
    """
    messages: [{"role":"system"|"user"|"assistant","content":"..."}]
    """
    model = model or OLLAMA_MODEL
    payload = {"model": model, "messages": messages, "stream": False}
    if json_mode:
        payload["format"] = "json"
    r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    out = r.json()["message"]["content"]
    return out

def tool_json(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Force JSON tool output. If bad JSON, tries one trim/repair.
    """
    raw = chat(messages, json_mode=True)
    try:
        return json.loads(raw)
    except Exception:
        # try to find outermost JSON
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end+1])
        raise
