import os, json, time, requests
from typing import Optional, Dict, Any, List, Generator
from dotenv import load_dotenv
load_dotenv()

LLM_BACKEND = os.getenv("LLM_BACKEND", "openai").strip().lower()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:1234/v1").rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "not-needed")
LLM_MODEL = os.getenv("LLM_MODEL", os.getenv("OLLAMA_MODEL", "llama3.1:8b"))
EMBED_MODEL = os.getenv("EMBED_MODEL", os.getenv("QA_EMBED_MODEL", "nomic-embed-text"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
TIMEOUT = int(os.getenv("LLM_TIMEOUT_S", "180"))
FALLBACK_MODELS = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if m.strip()]
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

def _candidate_models(model: Optional[str] = None) -> List[str]:
    primary = (model or LLM_MODEL).strip()
    out: List[str] = []
    for m in [primary, *FALLBACK_MODELS]:
        if m and m not in out:
            out.append(m)
    return out

def _chat_openai(messages: List[Dict[str, str]], model: str, json_mode: bool) -> str:
    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    r = requests.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def _chat_ollama(messages: List[Dict[str, str]], model: str, json_mode: bool) -> str:
    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if json_mode:
        payload["format"] = "json"
    r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["message"]["content"]

def chat(messages: List[Dict[str, str]], model: Optional[str] = None, json_mode: bool = False) -> str:
    last_err: Optional[Exception] = None
    dispatch = _chat_ollama if LLM_BACKEND == "ollama" else _chat_openai
    for candidate in _candidate_models(model):
        if _cb_is_open(candidate):
            continue
        try:
            out = dispatch(messages, candidate, json_mode)
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
    raw = chat(messages, json_mode=True)
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise

def embed(text: str) -> Optional[List[float]]:
    if not text:
        return None
    if LLM_BACKEND == "ollama":
        payload = {"model": EMBED_MODEL, "prompt": text}
        try:
            r = requests.post(f"{OLLAMA_HOST}/api/embeddings", json=payload, timeout=20)
            r.raise_for_status()
            vec = r.json().get("embedding")
            if isinstance(vec, list) and vec:
                return [float(x) for x in vec]
        except Exception:
            pass
        try:
            r = requests.post(f"{OLLAMA_HOST}/api/embed", json={"model": EMBED_MODEL, "input": text}, timeout=20)
            r.raise_for_status()
            emb = r.json().get("embeddings")
            if isinstance(emb, list) and emb and isinstance(emb[0], list):
                return [float(x) for x in emb[0]]
        except Exception:
            pass
        return None
    payload = {"model": EMBED_MODEL, "input": text}
    try:
        r = requests.post(
            f"{OPENAI_BASE_URL}/embeddings",
            json=payload,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            timeout=20,
        )
        r.raise_for_status()
        vec = r.json()["data"][0]["embedding"]
        if isinstance(vec, list):
            return [float(x) for x in vec]
    except Exception:
        return None
    return None

def stream_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Generator[Dict[str, Any], None, None]:
    model = model or LLM_MODEL
    if LLM_BACKEND == "ollama":
        yield from _stream_ollama(messages, model, options)
    else:
        yield from _stream_openai(messages, model, options)

def _stream_ollama(
    messages: List[Dict[str, str]], model: str, options: Optional[Dict[str, Any]] = None
) -> Generator[Dict[str, Any], None, None]:
    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if options:
        payload["options"] = options
    url = f"{OLLAMA_HOST}/api/chat"
    try:
        with requests.post(url, json=payload, stream=True, timeout=(5, 600)) as r:
            r.raise_for_status()
            yield {"event": "model", "data": {"model": model}}
            for raw in r.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    yield {"event": "delta", "data": raw}
                    continue
                if data.get("message") and isinstance(data["message"], dict):
                    piece = data["message"].get("content") or ""
                    if piece:
                        yield {"event": "delta", "data": piece}
                if data.get("done"):
                    stats = {k: v for k, v in {
                        "total_ms": data.get("total_duration"),
                        "eval_count": data.get("eval_count"),
                        "prompt_eval_count": data.get("prompt_eval_count"),
                    }.items() if v is not None}
                    yield {"event": "done", "data": stats}
                    break
    except requests.HTTPError as e:
        yield {"event": "error", "data": {"error": f"HTTP {e.response.status_code}", "detail": e.response.text[:500]}}
    except requests.RequestException as e:
        yield {"event": "error", "data": {"error": "Upstream unreachable", "detail": str(e)}}

def _stream_openai(
    messages: List[Dict[str, str]], model: str, options: Optional[Dict[str, Any]] = None
) -> Generator[Dict[str, Any], None, None]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if options:
        payload.update(options)
    url = f"{OPENAI_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=(5, 600)) as r:
            r.raise_for_status()
            yield {"event": "model", "data": {"model": model}}
            for raw in r.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if raw.startswith("data: "):
                    raw = raw[6:]
                if raw.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                choices = data.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield {"event": "delta", "data": content}
                finish = choices[0].get("finish_reason")
                if finish:
                    yield {"event": "done", "data": {"finish_reason": finish}}
                    break
    except requests.HTTPError as e:
        yield {"event": "error", "data": {"error": f"HTTP {e.response.status_code}", "detail": e.response.text[:500]}}
    except requests.RequestException as e:
        yield {"event": "error", "data": {"error": "Upstream unreachable", "detail": str(e)}}
