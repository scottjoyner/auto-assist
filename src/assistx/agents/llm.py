import os, json, time, requests
from typing import Optional, Dict, Any, List

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
TIMEOUT = int(os.getenv("LLM_TIMEOUT_S", "180"))
FALLBACK_MODELS = [m.strip() for m in os.getenv("OLLAMA_FALLBACK_MODELS", "").split(",") if m.strip()]
CB_FAIL_THRESHOLD = int(os.getenv("LLM_CB_FAIL_THRESHOLD", "3"))
CB_OPEN_S = int(os.getenv("LLM_CB_OPEN_SECONDS", "60"))
_CB_STATE: Dict[str, Dict[str, float]] = {}


def _cb_is_open(model: str) -> bool:
    st = _CB_STATE.get(model)
    if not st:
        return False
    return float(st.get("open_until", 0.0)) > time.time()


def _cb_on_success(model: str) -> None:
    _CB_STATE[model] = {"failures": 0.0, "open_until": 0.0}


def _cb_on_failure(model: str) -> None:
    st = _CB_STATE.setdefault(model, {"failures": 0.0, "open_until": 0.0})
    st["failures"] = float(st.get("failures", 0.0)) + 1.0
    if st["failures"] >= CB_FAIL_THRESHOLD:
        st["open_until"] = time.time() + CB_OPEN_S


def _candidate_models(model: Optional[str]) -> List[str]:
    primary = (model or OLLAMA_MODEL).strip()
    out: List[str] = []
    for m in [primary, *FALLBACK_MODELS]:
        if m and m not in out:
            out.append(m)
    return out

def chat(messages: List[Dict[str, str]], model: Optional[str] = None, json_mode: bool = False) -> str:
    """
    messages: [{"role":"system"|"user"|"assistant","content":"..."}]
    """
    last_err: Optional[Exception] = None
    for candidate in _candidate_models(model):
        if _cb_is_open(candidate):
            continue
        payload = {"model": candidate, "messages": messages, "stream": False}
        if json_mode:
            payload["format"] = "json"
        try:
            r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=TIMEOUT)
            r.raise_for_status()
            out = r.json()["message"]["content"]
            _cb_on_success(candidate)
            return out
        except Exception as e:
            _cb_on_failure(candidate)
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("No available LLM models (all circuit breakers open)")

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
