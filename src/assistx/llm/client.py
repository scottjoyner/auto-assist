import os, json, time, threading, requests
from typing import Optional, Dict, Any, List, Generator
from dotenv import load_dotenv
load_dotenv()

LLM_BACKEND = os.getenv("LLM_BACKEND", "openai").strip().lower()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:1234/v1").rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "not-needed")
LLM_MODEL = os.getenv("LLM_MODEL", os.getenv("OLLAMA_MODEL", "llama3.1:8b"))
EMBED_MODEL = os.getenv("EMBED_MODEL", os.getenv("QA_EMBED_QUERY_MODEL", "nomic-embed-text"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
TIMEOUT = int(os.getenv("LLM_TIMEOUT_S", "180"))
FALLBACK_MODELS = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if m.strip()]
CB_FAIL_THRESHOLD = int(os.getenv("LLM_CB_FAIL_THRESHOLD", "3"))
CB_OPEN_S = int(os.getenv("LLM_CB_OPEN_SECONDS", "60"))
_CB_STATE: Dict[str, Dict[str, float]] = {}

# --- Per-node speed tracking ------------------------------------------------
# Each worker process tracks how fast each responding endpoint is (exponential-
# moving average of request latency).  Nodes that are more than N× slower than
# the fleet median are skipped until their EMA decays or they are re-probed.
_NODE_EMA_ALPHA = float(os.getenv("LLM_NODE_EMA_ALPHA", "0.3"))
_NODE_SLOW_MULTIPLIER = float(os.getenv("LLM_NODE_SLOW_MULTIPLIER", "3.0"))
_NODE_REPROBE_AFTER_S = float(os.getenv("LLM_NODE_REPROBE_AFTER_S", "120.0"))
_node_ema: Dict[str, float] = {}       # base_url -> EMA of request latency (seconds)
_node_ema_at: Dict[str, float] = {}    # base_url -> last update timestamp
_node_ema_lock = threading.Lock()

# --- Fleet-aware routing ---------------------------------------------------
# The source of truth for "which model is loaded where" is each live LM Studio
# node's own /v1/models endpoint. fleet_state.json is used ONLY to enumerate the
# candidate node URLs (hostname + url); we then probe each node directly to find
# which ones actually have the requested model loaded & live. This avoids relying
# on a static snapshot that drifts from reality.
FLEET_STATE_PATH = os.getenv("FLEET_STATE_PATH", "/home/scott/git/lms/fleet_state.json")
# Free-tier cloud endpoint (assistx-only key) used as a review/orchestrator tier
# for non-sensitive traffic. Disabled unless OPENROUTER_API_KEY is set.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_fleet_lock = threading.Lock()
_fleet_nodes: List[str] = []                      # candidate base URLs (from state)
_fleet_nodes_at = 0.0
_fleet_loaded: Dict[str, List[str]] = {}          # model -> [base_url, ...] (probed)
_fleet_loaded_at: Dict[str, float] = {}           # model -> last probe time
_fleet_rr: Dict[str, int] = {}                    # model -> next index


def _node_urls_from_state() -> List[str]:
    """Enumerate candidate LM Studio base URLs from fleet_state.json."""
    try:
        with open(FLEET_STATE_PATH, "r") as f:
            state = json.load(f)
    except Exception:
        return []
    urls: List[str] = []
    for node in state.get("nodes", []):
        # Skip nodes that are stale AND localhost (127.0.0.1) — those are
        # truly gone.  Include stale nodes that have real network URLs (tailscale
        # etc.) because they may still be reachable and sitting idle.
        url = (node.get("url") or "").rstrip("/")
        if not url:
            continue
        if node.get("stale", False) and "127.0.0.1" in url:
            continue
        base = url.rsplit("/v1", 1)[0].rstrip("/") + "/v1"
        if base not in urls:
            urls.append(base)
    return urls


def _probe_loaded_models(base_url: str) -> Optional[List[str]]:
    """Return the list of model IDs actually loaded on a node, or None if down."""
    try:
        r = requests.get(f"{base_url}/models", timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        out: List[str] = []
        for m in data.get("data", []):
            mid = m.get("id") or m.get("name") or m.get("model")
            if mid:
                out.append(mid)
        return out
    except Exception:
        return None


def _refresh_fleet_nodes() -> None:
    global _fleet_nodes, _fleet_nodes_at
    now = time.time()
    if now - _fleet_nodes_at < 120 and _fleet_nodes:
        return
    with _fleet_lock:
        _fleet_nodes = _node_urls_from_state()
        _fleet_nodes_at = now


def _loaded_nodes_for(model: str) -> List[str]:
    """Probe live nodes and return base URLs that currently have `model` loaded.

    Cached per-model for 60s so we don't hammer /v1/models on every call."""
    _refresh_fleet_nodes()
    now = time.time()
    with _fleet_lock:
        cached = _fleet_loaded.get(model)
        if cached is not None and now - _fleet_loaded_at.get(model, 0) < 60:
            return list(cached)
    nodes = _fleet_nodes or [OPENAI_BASE_URL]
    mlow = model.lower()
    loaded: List[str] = []
    for base in nodes:
        ids = _probe_loaded_models(base)
        if not ids:
            continue
        if any(mlow in mid.lower() for mid in ids):
            loaded.append(base)
    with _fleet_lock:
        _fleet_loaded[model] = loaded
        _fleet_loaded_at[model] = now
    return loaded


def _track_node_latency(base_url: str, latency_s: float) -> None:
    """Update the exponential-moving-average latency for *base_url*."""
    with _node_ema_lock:
        prev = _node_ema.get(base_url)
        if prev is None:
            _node_ema[base_url] = latency_s
        else:
            _node_ema[base_url] = (1.0 - _NODE_EMA_ALPHA) * prev + _NODE_EMA_ALPHA * latency_s
        _node_ema_at[base_url] = time.time()


def _filter_slow_nodes(urls: List[str]) -> List[str]:
    """Return only URLs whose EMA latency is within *slow_multiplier* × median.

    Nodes that have never been measured (or whose EMA is stale) are always
    kept, so a new or recovered node is never permanently excluded.
    """
    if len(urls) < 3:
        return list(urls)
    now = time.time()
    measured: List[float] = []
    with _node_ema_lock:
        for u in urls:
            v = _node_ema.get(u)
            if v is not None and now - _node_ema_at.get(u, 0) < _NODE_REPROBE_AFTER_S:
                measured.append(v)
    if len(measured) < 2:
        return list(urls)
    measured.sort()
    median = measured[len(measured) // 2]
    threshold = median * _NODE_SLOW_MULTIPLIER
    out: List[str] = []
    with _node_ema_lock:
        for u in urls:
            v = _node_ema.get(u)
            if v is None or now - _node_ema_at.get(u, 0) >= _NODE_REPROBE_AFTER_S:
                out.append(u)          # never measured or data too old → keep
            elif v <= threshold:
                out.append(u)          # fast enough
    return out


def fleet_base_urls_for(model: str) -> List[str]:
    """Return base URLs that have `model` loaded (probed live), shuffled randomly,
    with the local endpoint and (if configured) OpenRouter free tier appended.

    Slow nodes (measured EMA latency > ``LLM_NODE_SLOW_MULTIPLIER`` × median)
    are excluded until their EMA decays or *LLM_NODE_REPROBE_AFTER_S* elapses.
    """
    loaded = _loaded_nodes_for(model)
    # Shuffle so the ~32 workers spread across all available nodes instead of
    # round-robining through a broken per-process counter.
    import random
    urls = random.sample(loaded, len(loaded)) if loaded else []
    # Local endpoint first as a fast default if it has the model (or as fallback).
    if OPENAI_BASE_URL not in urls:
        urls.append(OPENAI_BASE_URL)
    # Free-tier review/orchestrator tier for non-sensitive traffic.
    if OPENROUTER_API_KEY:
        urls.append(OPENROUTER_BASE_URL)
    return _filter_slow_nodes(urls)


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

def _chat_openai(messages: List[Dict[str, str]], model: str, json_mode: bool, base_url: Optional[str] = None) -> str:
    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if json_mode:
        # LM Studio (OpenAI-compatible) rejects "json_object"; it requires
        # "json_schema". A permissive schema with strict=false lets the model
        # return arbitrary JSON while still forcing JSON-only output.
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "assistx_json",
                "strict": False,
                "schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            },
        }
    base = (base_url or OPENAI_BASE_URL).rstrip("/")
    if "openrouter.ai" in base:
        auth = f"Bearer {OPENROUTER_API_KEY}" if OPENROUTER_API_KEY else OPENAI_API_KEY
        extra = {"HTTP-Referer": "https://assistx.local", "X-Title": "assistx"}
    else:
        auth = f"Bearer {OPENAI_API_KEY}"
        extra = {}
    r = requests.post(
        f"{base}/chat/completions",
        json=payload,
        headers={"Authorization": auth, **extra},
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
    # Hard wall-clock budget for the whole chat attempt so a hang on one fleet
    # node can't wedge a task for minutes. Per-call timeout is honored, but we
    # also bail out of the node/model loop once the budget is spent.
    deadline = time.time() + max(20, TIMEOUT)
    for candidate in _candidate_models(model):
        if _cb_is_open(candidate):
            continue
        # Spread across the Tailscale LM Studio fleet (plus local), trying each
        # base URL that has this model loaded before moving to the next model.
        base_urls = fleet_base_urls_for(candidate) or [OPENAI_BASE_URL]
        for base_url in base_urls:
            if time.time() > deadline:
                break
            t0 = time.time()
            try:
                out = dispatch(messages, candidate, json_mode, base_url=base_url)
                _cb_on_success(candidate)
                _track_node_latency(base_url, time.time() - t0)
                return out
            except Exception as e:
                _cb_on_failure(candidate)
                if base_url != OPENAI_BASE_URL:
                    _track_node_latency(base_url, time.time() - t0)
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
