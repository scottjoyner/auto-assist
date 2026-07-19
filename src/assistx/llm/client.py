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
TIMEOUT = int(os.getenv("LLM_TIMEOUT_S", "30"))
_COLD_START_TIMEOUT = 8  # short timeout for probing whether a model is hot
FALLBACK_MODELS = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if m.strip()]
CB_FAIL_THRESHOLD = int(os.getenv("LLM_CB_FAIL_THRESHOLD", "3"))
CB_OPEN_S = int(os.getenv("LLM_CB_OPEN_SECONDS", "60"))
_CB_STATE: Dict[str, Dict[str, float]] = {}

# --- Reasoning-content capture (thread-local) --------------------------------
_reasoning_local = threading.local()

def _set_reasoning_content(text: str) -> None:
    _reasoning_local.reasoning_content = text

def last_reasoning_content() -> str:
    """Return the *reasoning_content* field from the most recent chat response
    on this thread.  Returns empty string if none was present."""
    return getattr(_reasoning_local, "reasoning_content", "")

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

# --- Per-node failure tracking -----------------------------------------------
_node_results: Dict[str, List[bool]] = {}  # base_url -> sliding window of successes (True) / failures (False)
_NODE_RESULTS_WINDOW = int(os.getenv("LLM_NODE_RESULTS_WINDOW", "50"))
_NODE_MAX_FAILURE_RATE = float(os.getenv("LLM_NODE_MAX_FAILURE_RATE", "0.5"))
_node_results_lock = threading.Lock()

# --- Per-(model, node) pair tracking ----------------------------------------
# Remember which (model, node) combos have recently failed so we don't waste
# time retrying combos that are known to be broken (e.g. MTP conflict on a
# node that can't load the requested model alongside its primary workload).
_pair_results: Dict[str, float] = {}   # "model|base_url" -> time of last failure
_PAIR_BACKOFF_S = int(os.getenv("LLM_PAIR_BACKOFF_S", "300"))  # 5 min blacklist
_pair_lock = threading.Lock()

def _mark_pair_failed(model: str, base_url: str) -> None:
    with _pair_lock:
        _pair_results[f"{model}|{base_url}"] = time.time()

def _is_pair_blacklisted(model: str, base_url: str) -> bool:
    with _pair_lock:
        ts = _pair_results.get(f"{model}|{base_url}")
        if ts is None:
            return False
        return (time.time() - ts) < _PAIR_BACKOFF_S


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
_fleet_inventory: Dict[str, dict] = {}            # model -> {"hot": [urls], "cold": [urls]}
_fleet_inventory_at: float = 0.0
LLM_MODEL_MIX = os.getenv("LLM_MODEL_MIX", "1") == "1"  # enable model mixing


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


_EMBED_KEYWORDS = ("embedding", "nomic-embed", "text-embedding")

def _is_embedding_model(model_id: str) -> bool:
    ml = model_id.lower()
    return any(kw in ml for kw in _EMBED_KEYWORDS)


def _probe_loaded_models(base_url: str) -> Optional[List[str]]:
    """Return the list of model IDs actually loaded on a node, or None if down.

    Embedding models are excluded from the returned list so they never appear
    as candidates for chat/tool-call routing.
    """
    try:
        r = requests.get(f"{base_url}/models", timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        out: List[str] = []
        for m in data.get("data", []):
            mid = m.get("id") or m.get("name") or m.get("model")
            if mid and not _is_embedding_model(mid):
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


def _fleet_model_inventory() -> Dict[str, list]:
    """Probe all fleet nodes in parallel and return models that are HOT
    (confirmed loaded in GPU, not just on disk).

    Returns {model_name: [base_urls...]} — every model confirmed hot on at
    least one node.  Cached for 60s.  Only includes models that respond
    quickly to a tiny completion (< COLD_START_TIMEOUT), which avoids
    sending traffic to models that would trigger an MTP conflict or
    prolonged cold-start load.
    """
    global _fleet_inventory, _fleet_inventory_at
    now = time.time()
    if now - _fleet_inventory_at < 60 and _fleet_inventory:
        return dict(_fleet_inventory)
    _refresh_fleet_nodes()
    nodes = _fleet_nodes or [OPENAI_BASE_URL]
    inventory: Dict[str, list] = {}
    import concurrent.futures as cf

    def _probe_and_verify(base):
        """Get model list, then verify each is hot via a tiny chat completion."""
        ids = _probe_loaded_models(base)
        if not ids:
            return base, []
        hot = []
        for mid in ids:
            try:
                t0 = time.time()
                r = requests.post(f"{base}/chat/completions", json={
                    "model": mid, "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1, "temperature": 0, "stream": False,
                }, timeout=_COLD_START_TIMEOUT)
                elapsed = (time.time() - t0) * 1000
                if r.status_code == 200 and elapsed < _COLD_START_TIMEOUT * 1000 * 0.9:
                    hot.append(mid)
            except requests.RequestException:
                pass
        return base, hot

    with cf.ThreadPoolExecutor(max_workers=min(len(nodes) or 1, 16)) as pool:
        results = list(pool.map(_probe_and_verify, nodes))
    for base, hot_ids in results:
        for mid in hot_ids:
            inventory.setdefault(mid, []).append(base)
    with _fleet_lock:
        _fleet_inventory = inventory
        _fleet_inventory_at = now
    return inventory


def _select_model_weighted(inventory: Dict[str, list]) -> List[str]:
    """Return all model names in weighted-random order so models available on
    more nodes get proportionally more traffic.

    Weight = node_count * 3 for exploration coverage (every model on >=1 node
    gets traffic proportional to its fleet footprint).
    """
    import random as _random
    items = [(mid, max(len(urls) * 3.0, 1.0)) for mid, urls in inventory.items()]
    if not items:
        return []
    _random.shuffle(items)
    result = []
    remaining = list(items)
    while remaining:
        total = sum(w for _, w in remaining)
        r = _random.random() * total
        cumulative = 0.0
        for idx, (mid, w) in enumerate(remaining):
            cumulative += w
            if r <= cumulative:
                result.append(mid)
                remaining.pop(idx)
                break
    return result


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


def _track_node_result(base_url: str, ok: bool) -> None:
    """Record a success or failure for *base_url* in a sliding window."""
    with _node_results_lock:
        buf = _node_results.setdefault(base_url, [])
        buf.append(ok)
        if len(buf) > _NODE_RESULTS_WINDOW:
            buf.pop(0)


def _node_failure_rate(base_url: str) -> Optional[float]:
    """Return the failure rate (0..1) for *base_url*, or None if insufficient data."""
    with _node_results_lock:
        buf = _node_results.get(base_url)
        if not buf or len(buf) < 5:
            return None
        failures = sum(1 for r in buf if not r)
        return failures / len(buf)


def _filter_high_failure_nodes(urls: List[str]) -> List[str]:
    """Exclude nodes whose recent failure rate exceeds the configured threshold.

    Nodes with fewer than 5 observations are always kept (cold start). The
    failure rate is checked before the slow-node exclusion because a failing
    node is worse than a slow one.
    """
    if len(urls) < 2:
        return list(urls)
    out: List[str] = []
    for u in urls:
        fr = _node_failure_rate(u)
        if fr is None or fr <= _NODE_MAX_FAILURE_RATE:
            out.append(u)
    return out


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


def _weighted_shuffle(urls: List[str]) -> List[str]:
    """Order URLs so faster nodes (lower EMA latency) appear earlier with high
    probability, while still preserving randomness so all workers don't pile on
    the same node.

    Each URL receives an *inverse-latency* weight; unmeasured nodes get the
    median weight so they are not starved.  Weighted random ordering smooths
    load across heterogeneous nodes: a node 2× faster gets ~2× the traffic.
    """
    if len(urls) < 2:
        return list(urls)
    import random as _random
    now = time.time()
    weights: List[float] = []
    with _node_ema_lock:
        # Compute weights
        for u in urls:
            v = _node_ema.get(u)
            if v is not None and now - _node_ema_at.get(u, 0) < _NODE_REPROBE_AFTER_S:
                w = 1.0 / max(v, 0.01)   # inverse latency → higher is faster
            else:
                w = 0.0                   # unmeasured / stale → will get median weight
            weights.append(w)
    # Assign median weight to unmeasured/stale nodes so they aren't excluded.
    measured_w = [w for w in weights if w > 0.0]
    if measured_w:
        median_w = sorted(measured_w)[len(measured_w) // 2]
        weights = [w if w > 0.0 else median_w for w in weights]
    else:
        return list(urls)
    # Weighted random permutation: repeatedly pick from remaining with P ∝ weight.
    remaining = list(range(len(urls)))
    result: List[str] = []
    while remaining:
        total = sum(weights[i] for i in remaining)
        if total <= 0:
            result.extend(urls[i] for i in remaining)
            break
        r = _random.random() * total
        cumulative = 0.0
        for idx, pos in enumerate(remaining):
            cumulative += weights[pos]
            if r <= cumulative:
                result.append(urls[pos])
                remaining.pop(idx)
                break
    return result


def fleet_base_urls_for(model: str) -> List[str]:
    """Return base URLs, in latency-weighted random order, that have `model`
    loaded (probed live) with the local endpoint and (if configured) OpenRouter
    appended as trailing fallback.

    Slow nodes (EMA > ``LLM_NODE_SLOW_MULTIPLIER`` × median) are excluded.
    Among the remaining candidates the weighted shuffle means a node 2× faster
    gets ~2× the traffic.
    """
    loaded = _loaded_nodes_for(model)
    # Exclude high-failure nodes first, then slow nodes, then arrange by speed.
    healthy = _filter_high_failure_nodes(loaded) if loaded else []
    fit = _filter_slow_nodes(healthy) if healthy else []
    urls = _weighted_shuffle(fit) if len(fit) > 1 else list(fit)
    # Local endpoint last (fallback) since it always has the model.
    if OPENAI_BASE_URL not in urls:
        urls.append(OPENAI_BASE_URL)
    # Free-tier review/orchestrator tier for non-sensitive traffic.
    if OPENROUTER_API_KEY:
        urls.append(OPENROUTER_BASE_URL)
    return urls


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
    if model is None and LLM_MODEL_MIX:
        # Mix across ALL fleet-available models so traffic distributes
        # naturally.  The weighted random order means models on more nodes
        # appear earlier (and thus get tried first) proportionally more
        # often, but every model gets explored.
        inventory = _fleet_model_inventory()
        mixed = _select_model_weighted(inventory)
        for m in mixed:
            if m and m not in out:
                out.append(m)
        # Ensure the configured default is always somewhere in the list
        # (not necessarily first) so it remains in rotation.
        if primary not in out:
            out.append(primary)
    else:
        out.append(primary)
    for m in FALLBACK_MODELS:
        if m and m not in out:
            out.append(m)
    return out

def _chat_openai(messages: List[Dict[str, str]], model: str, json_mode: bool, base_url: Optional[str] = None, _timeout_override: Optional[int] = None) -> str:
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
        timeout=_timeout_override or TIMEOUT,
    )
    r.raise_for_status()
    resp_data = r.json()
    msg = resp_data["choices"][0]["message"]
    _set_reasoning_content(msg.get("reasoning_content", ""))
    return msg["content"]

def _chat_ollama(messages: List[Dict[str, str]], model: str, json_mode: bool, base_url: Optional[str] = None, _timeout_override: Optional[int] = None) -> str:
    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if json_mode:
        payload["format"] = "json"
    r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=_timeout_override or TIMEOUT)
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
        base_urls = fleet_base_urls_for(candidate) or [OPENAI_BASE_URL]
        for base_url in base_urls:
            if time.time() > deadline:
                break
            # Skip (model, node) pairs that have recently failed (MTP conflict,
            # model not actually loadable on this node).
            if base_url != OPENAI_BASE_URL and _is_pair_blacklisted(candidate, base_url):
                continue
            # Use a short probe timeout for unconfirmed (model, node) pairs so
            # a cold-start or MTP conflict doesn't clog the worker for minutes.
            pair_key = f"{candidate}|{base_url}"
            with _pair_lock:
                known_good = pair_key not in _pair_results
            effective_timeout = _COLD_START_TIMEOUT if known_good else TIMEOUT
            t0 = time.time()
            try:
                out = dispatch(messages, candidate, json_mode, base_url=base_url,
                              _timeout_override=effective_timeout)
                _cb_on_success(candidate)
                _track_node_latency(base_url, time.time() - t0)
                _track_node_result(base_url, True)
                return out
            except Exception as e:
                _cb_on_failure(candidate)
                if base_url != OPENAI_BASE_URL:
                    _track_node_latency(base_url, time.time() - t0)
                    _track_node_result(base_url, False)
                    _mark_pair_failed(candidate, base_url)
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
