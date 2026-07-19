import os, json, time, threading, requests
from typing import Optional, Dict, Any, List, Generator
from dotenv import load_dotenv
load_dotenv()

# Import Neo4jClient at module top-level (NOT lazily inside functions).  Lazy
# imports from a background thread race with the main import lock and deadlock
# the loader.  Top-level import guarantees it is loaded exactly once, before
# the loader thread starts.
try:
    from assistx.neo4j_client import Neo4jClient
except Exception:
    Neo4jClient = None
LLM_BACKEND = os.getenv("LLM_BACKEND", "openai").strip().lower()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:1234/v1").rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "not-needed")
LLM_MODEL = os.getenv("LLM_MODEL", os.getenv("OLLAMA_MODEL", "llama3.1:8b"))
EMBED_MODEL = os.getenv("EMBED_MODEL", os.getenv("QA_EMBED_QUERY_MODEL", "nomic-embed-text"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
TIMEOUT = int(os.getenv("LLM_TIMEOUT_S", "30"))
# HTTP timeout used when probing whether a model is hot.  Long enough to let a
# model that is genuinely loaded respond, but short enough that a model which is
# NOT loaded (and would need a multi-second cold load) is correctly skipped.
_COLD_START_TIMEOUT = 8
# A model is considered HOT (resident in GPU) only if it answers a 1-token
# completion in under this many seconds AND the response body matches.
_HOT_THRESHOLD_S = float(os.getenv("LLM_HOT_THRESHOLD_S", "2.0"))
# We NEVER route to the host / localhost.  The host box is reserved for
# finetuning and has no usable LLM endpoint.  Routing is fleet-only.
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
FLEET_ONLY = os.getenv("LLM_FLEET_ONLY", "1") == "1"  # default: never use host
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
# Soft consecutive-failure suppression.  We do NOT blacklist a pair just because
# a request timed out while the model was cold-loading — that is NORMAL and the
# loader is responsible for warming models.  We only suppress a (model, node)
# pair after it has produced N *hard* consecutive failures (4xx/5xx or connection
# refused), which indicates a genuinely broken combination (e.g. MTP conflict,
# model file corrupt).  Any success resets the counter.  This prevents the old
# behaviour of hammering a node with cold-load attempts and then blacklisting it
# for 5 minutes while it was still loading.
_pair_failures: Dict[str, int] = {}   # "model|base_url" -> consecutive hard failures
_PAIR_HARD_FAIL_LIMIT = int(os.getenv("LLM_PAIR_HARD_FAIL_LIMIT", "4"))
_pair_lock = threading.Lock()

def _mark_pair_failed(model: str, base_url: str, hard: bool = True) -> None:
    """Record an attempt against a (model, node) pair.

    hard=True  → a genuine error (4xx/5xx/connection refused). Counts toward
                 suppression. After _PAIR_HARD_FAIL_LIMIT consecutive hard
                 failures the pair is skipped (model simply won't be routed
                 there) until it succeeds once.
    hard=False → a timeout / cold-load miss. Does NOT count as a failure; we
                 simply don't route there this round. The loader handles warming.
    """
    with _pair_lock:
        key = f"{model}|{base_url}"
        if hard:
            _pair_failures[key] = _pair_failures.get(key, 0) + 1
        else:
            # a soft miss leaves the counter untouched
            pass

def _mark_pair_ok(model: str, base_url: str) -> None:
    with _pair_lock:
        _pair_failures.pop(f"{model}|{base_url}", None)

def _is_pair_suppressed(model: str, base_url: str) -> bool:
    with _pair_lock:
        return _pair_failures.get(f"{model}|{base_url}", 0) >= _PAIR_HARD_FAIL_LIMIT

def _pair_failure_count(model: str, base_url: str) -> int:
    with _pair_lock:
        return _pair_failures.get(f"{model}|{base_url}", 0)


# --- Fleet-aware routing ---------------------------------------------------
# ROUTING IS FLEET-ONLY.  The host box is reserved for finetuning and has no
# usable LLM endpoint, so we never route there.  The source of truth for the
# fleet topology is the knowledge graph (:ModelEndpoint nodes with status=online),
# NOT a static fleet_state.json (which does not exist on this host).  We then
# probe each live node to confirm which models are actually HOT (resident in GPU)
# before routing any traffic to them.  Workers NEVER trigger cold loads — that is
# the loader thread's job.  This keeps the fleet stable: workers only ever hit
# models we have positively confirmed are resident.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_fleet_lock = threading.Lock()
_fleet_nodes: List[str] = []                      # candidate base URLs (from Neo4j)
_fleet_nodes_at = 0.0
_fleet_loaded: Dict[str, List[str]] = {}          # model -> [base_url, ...] (probed)
_fleet_loaded_at: Dict[str, float] = {}           # model -> last probe time
_fleet_rr: Dict[str, int] = {}                    # model -> next index
_fleet_inventory: Dict[str, dict] = {}            # model -> {"hot": [urls], "cold": [urls]}
_fleet_inventory_at: float = 0.0
LLM_MODEL_MIX = os.getenv("LLM_MODEL_MIX", "1") == "1"  # enable model mixing


def _is_local_url(url: str) -> bool:
    u = (url or "").lower()
    return any(seg in u for seg in _LOCAL_HOSTS) or u.startswith("http://localhost") \
        or u.startswith("http://127.") or u.startswith("http://0.")


def _node_urls_from_state() -> List[str]:
    """Enumerate candidate LM Studio base URLs from the knowledge graph.

    Reads :ModelEndpoint nodes that are online and serve chat, returns their
    base URLs (with /v1 appended).  Excludes any localhost/host URLs so we
    never route onto the finetuning box.  Falls back to an empty list (never
    to localhost) so that when the fleet is unreachable we simply serve no
    traffic rather than hammering the host.

    Uses the loader-private Neo4j driver (_loader_neo) which we have verified
    connects reliably from background threads; the request path keeps
    _fleet_nodes fresh via its own shared driver.
    """
    try:
        client = _loader_neo()
        with client._session() as s:
            res = s.run("""
                MATCH (e:ModelEndpoint)
                WHERE e.status = 'online'
                  AND (e.purpose IS NULL OR e.purpose CONTAINS 'llm' OR e.purpose = 'model_endpoint')
                RETURN e.base_url AS base_url, e.node_id AS node_id
            """)
            urls: List[str] = []
            for row in res:
                raw = (row.get("base_url") or "").strip()
                if not raw:
                    continue
                if _is_local_url(raw):
                    continue
                base = raw.rstrip("/")
                if "/v1" not in base:
                    base = base + "/v1"
                if base not in urls:
                    urls.append(base)
            return urls
    except Exception:
        return []


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


# --- Loader / routing shared state ------------------------------------------
# The autonomous loader (see _fleet_loader_loop) owns the decision of WHICH
# models should be hot on the fleet.  It maintains:
#   _loader_target_models : set of model ids the loader wants resident somewhere
#   _loader_node_targets   : {node_base_url: set(model_ids)} desired per node
#   _loader_state          : status dict for the UI (/api/fleet/loader/status)
# Routing's hot-inventory probe ONLY checks models in _loader_target_models, so
# we never send a completion request to a model we haven't deliberately decided
# to load.  This is what stops the fleet from being hammered.
_loader_target_models: set = set()
_loader_node_targets: Dict[str, set] = {}
# Operator-pinned wishlist (set from the UI).  When non-empty the loader loads
# exactly these models instead of auto-discovering smallest-first.  Empty = let
# the loader decide.  The operator is in control, not the heuristic.
_loader_pinned_models: set = set()
# (base_url, model_id) -> native instance_id, so a model can be unloaded later.
_loader_instance_ids: Dict[tuple, str] = {}
# Ownership + config tracking so the loader MIRRORS the operator's manual layout
# instead of clobbering it.  The loader only ever removes models IT loaded
# (owner == "assistx"); models the operator loaded manually (owner == "user")
# are treated as part of the desired layout and preserved.
#   _loader_owned : (base, model_id) -> "assistx" | "user"
#   _loader_node_configs : (base, model_id) -> LM Studio load config dict
#                          (context_length, speculative_draft_mtp, etc.)
#   _loader_user_configs : (base, model_id) -> snapshotted config from a manual
#                          load, reused when assistx reloads that model later.
#   _loader_demand : set of model_ids some subsystem (e.g. portfolio trader)
#                    has asked to be resident, even with no pinned wishlist.
_loader_owned: Dict[tuple, str] = {}
_loader_node_configs: Dict[tuple, dict] = {}
_loader_user_configs: Dict[tuple, dict] = {}
_loader_demand: set = set()
_loader_state: Dict[str, Any] = {
    "running": False, "last_run_ts": 0.0, "last_action": "",
    "cycle": 0, "discovered_models": [], "per_node": {}, "owners": {},
}
_loader_lock = threading.Lock()


def _fleet_model_inventory() -> Dict[str, list]:
    """Return models that are HOT (confirmed resident in GPU) across the fleet.

    This is the single source of truth for routing.  A model is HOT only if it
    answers a 1-token completion in < _HOT_THRESHOLD_S with a matching `model`
    field and valid content.  Critically, we only PROBE models that are in the
    loader's target set (_loader_target_models) — never every on-disk model —
    so we don't accidentally trigger cold loads on models we have no intention
    of using.

    Cached for 60s to avoid probing on every chat call.
    """
    global _fleet_inventory, _fleet_inventory_at
    now = time.time()
    with _fleet_lock:
        if now - _fleet_inventory_at < 60 and _fleet_inventory:
            return dict(_fleet_inventory)
    _refresh_fleet_nodes()
    nodes = _fleet_nodes or []
    if not nodes:
        # No fleet reachable: return empty.  We do NOT fall back to localhost.
        with _fleet_lock:
            _fleet_inventory = {}
            _fleet_inventory_at = now
        return {}

    with _loader_lock:
        candidate_models = list(_loader_target_models)

    inventory: Dict[str, list] = {}
    import concurrent.futures as cf

    def _probe_and_verify(base):
        """Confirm which candidate models are hot on this node right now."""
        if not candidate_models:
            return base, []
        hot = []
        for mid in candidate_models:
            try:
                t0 = time.time()
                r = requests.post(f"{base}/chat/completions", json={
                    "model": mid, "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1, "temperature": 0, "stream": False,
                }, timeout=_COLD_START_TIMEOUT)
                elapsed = (time.time() - t0) * 1000
                if r.status_code != 200 or elapsed >= _HOT_THRESHOLD_S * 1000:
                    continue
                body = r.json()
                if not isinstance(body, dict):
                    continue
                responded_model = body.get("model", "")
                if responded_model and mid not in responded_model:
                    continue
                choices = body.get("choices", [])
                if not choices or not isinstance(choices, list):
                    continue
                msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
                if not msg.get("content"):
                    continue
                hot.append(mid)
            except (requests.RequestException, json.JSONDecodeError, LookupError, TypeError):
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
    """Return base URLs that currently have `model` HOT (resident in GPU).

    Derived purely from the cached hot inventory — no extra /v1/models probing,
    so this is cheap and never triggers a cold load.  If the model isn't in the
    inventory it simply isn't loaded anywhere; routing will skip it.
    """
    inv = _fleet_model_inventory()
    return list(inv.get(model, []))


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
    """Return fleet base URLs (hot, healthy, fast-ordered) that have `model`
    resident in GPU right now.

    FLEET-ONLY: we never append the localhost / OPENAI_BASE_URL endpoint (the
    host is reserved for finetuning) and we never append OpenRouter (disabled).
    If the model is not hot anywhere, returns [] — the caller then tries the
    next candidate model or raises.  We never attempt a cold load here.

    Ordering favours nodes that, historically, serve this model with the best
    blend of quality and speed (learned from recorded per-(model,node) perf).
    """
    loaded = _loaded_nodes_for(model)
    if not loaded:
        return []
    # Drop (model, node) pairs that have produced repeated HARD failures.
    healthy = [u for u in loaded if not _is_pair_suppressed(model, u)]
    if not healthy:
        return []
    fit = _filter_slow_nodes(healthy)
    urls = _weighted_shuffle(fit) if len(fit) > 1 else list(fit)
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

# --- Learned performance (quality × speed) ---------------------------------
# Every completed chat records observed tokens/sec + latency for the
# (model, node) pair into the knowledge graph as a :ModelPerf node.  Over time
# this builds the fleet's shared understanding of which models are fast vs
# high-quality, which the router uses to weight traffic.  We keep an in-process
# cache so we don't hit Neo4j on every ranking call.
_model_perf_cache: Dict[str, Dict[str, float]] = {}  # "model|base" -> {tps,lat,q}
_model_perf_cache_at = 0.0
_model_perf_lock = threading.Lock()
_PERF_CACHE_TTL = 30.0
# Weights for the quality×speed blend.  Tunable: raise QUALITY_WEIGHT to favour
# correctness over throughput (slow dense models beat many small iterations).
_PERF_QUALITY_WEIGHT = float(os.getenv("LLM_PERF_QUALITY_WEIGHT", "0.6"))
_PERF_SPEED_WEIGHT = float(os.getenv("LLM_PERF_SPEED_WEIGHT", "0.4"))


def _record_perf(model: str, base_url: str, tps: Optional[float],
                 latency_s: Optional[float], ok: bool, tokens: int = 0) -> None:
    """Persist an observed (model, node) performance sample to the KG.

    Writes a :ModelPerf node keyed by (model, node) with EMA-smoothed tps and
    latency, plus a quality_score that starts neutral and is nudged by success.
    Failures lower quality_score so bad combos are deprioritised.  Best-effort:
    any KG error is swallowed so routing is never blocked.
    """
    node_id = base_url.rsplit("/v1", 1)[0].rsplit("/", 1)[-1] if "/v1" in base_url else base_url
    key = f"{model}|{base_url}"
    with _model_perf_lock:
        _model_perf_cache[key] = {
            "tps": tps or 0.0, "lat": latency_s or 0.0, "q": 1.0 if ok else 0.2,
        }
        _model_perf_cache_at = time.time()
    try:
        client = Neo4jClient()
        with client._session() as s:
            s.run("""
                MERGE (p:ModelPerf {model:$model, node:$node})
                ON CREATE SET p.tps=0.0, p.latency_ms=0.0, p.quality_score=1.0,
                              p.runs=0, p.last_used_ts=$ts, p.created_at_ts=$ts
                ON MATCH SET p.last_used_ts=$ts
                SET p.runs = p.runs + 1,
                    p.tps = CASE WHEN $tps > 0 THEN (p.tps*0.7 + $tps*0.3) ELSE p.tps END,
                    p.latency_ms = CASE WHEN $lat > 0 THEN (p.latency_ms*0.7 + $lat*1000*0.3) ELSE p.latency_ms END,
                    p.quality_score = CASE WHEN $ok THEN
                        (p.quality_score*0.8 + 1.0*0.2) ELSE (p.quality_score*0.8 + 0.2*0.2) END
            """, model=model, node=node_id, ts=int(time.time() * 1000),
                 tps=tps or 0.0, lat=latency_s or 0.0, ok=ok)
    except Exception:
        pass


def _model_perf_blend(model: str):
    """Return (quality_score, latency_s) blended across nodes for `model`,
    or (None, None) if no learned data exists yet."""
    global _model_perf_cache_at
    now = time.time()
    with _model_perf_lock:
        if now - _model_perf_cache_at > _PERF_CACHE_TTL:
            try:
                client = Neo4jClient()
                with client._session() as s:
                    res = s.run("""
                        MATCH (p:ModelPerf {model:$model})
                        RETURN p.latency_ms AS lat, p.tps AS tps, p.quality_score AS q
                    """, model=model)
                    for row in res:
                        node = "?"  # we only need aggregate; key by model+base not tracked here
                        _model_perf_cache[f"{model}|{node}"] = {
                            "tps": row.get("tps") or 0.0,
                            "lat": (row.get("lat") or 0.0) / 1000.0,
                            "q": row.get("q") or 1.0,
                        }
                _model_perf_cache_at = now
            except Exception:
                pass
    # Aggregate across cached entries for this model.
    qs, lats, n = [], [], 0
    with _model_perf_lock:
        for k, v in _model_perf_cache.items():
            if k.startswith(f"{model}|"):
                if v["q"]:
                    qs.append(v["q"])
                if v["lat"]:
                    lats.append(v["lat"])
                n += 1
    if not qs:
        return None, None
    avg_q = sum(qs) / len(qs)
    avg_lat = sum(lats) / len(lats) if lats else None
    return avg_q, avg_lat


def _candidate_models(model: Optional[str] = None) -> List[str]:
    """Return the models we are willing to route to, in preferred order.

    FLEET-ONLY and HOT-ONLY: every model returned is one we have positively
    confirmed is resident in GPU somewhere on the fleet (via the hot inventory).
    We never include a model that isn't hot, so workers never attempt a cold
    load.  The order favours models with the best learned quality×speed score
    across the fleet, with randomness so multiple workers spread out.
    """
    primary = (model or LLM_MODEL).strip()
    inventory = _fleet_model_inventory()
    if not inventory:
        # Nothing hot on the fleet right now.  Return empty — chat() will then
        # raise a clear error rather than hammering localhost.
        return []

    if model is not None and model in inventory:
        # Explicit model requested AND it's hot somewhere: use it first.
        return [model] + _rank_models(inventory, exclude={model})

    if model is not None:
        # Explicit model requested but it is NOT hot anywhere.  Don't fabricate
        # a cold load — fall through to the ranked hot set so the task still
        # gets served by something that works.
        pass

    # Model mixing: rank all hot models by learned quality×speed.
    ranked = _rank_models(inventory)
    if primary in inventory and primary not in ranked:
        ranked.append(primary)
    return ranked


def _rank_models(inventory: Dict[str, list], exclude: set = set()) -> List[str]:
    """Order hot models by fleet-wide learned quality×speed score.

    Score = mean over nodes( quality_score / max(latency_s, 0.05) ), blended
    with fleet availability (more nodes → slightly preferred for resilience).
    Falls back to availability-only ranking when no learned scores exist yet,
    so the system works on day one before feedback accumulates.
    """
    import random as _random
    scored = []
    for mid, urls in inventory.items():
        if mid in exclude:
            continue
        q, lat = _model_perf_blend(mid)
        # availability weight: more nodes = more resilient
        avail = 1.0 + 0.15 * (len(urls) - 1)
        if q is not None and lat is not None:
            score = (q / max(lat, 0.05)) * avail
        else:
            # No learned data yet: prefer smaller/faster-looking + availability.
            score = avail * (1.0 / max(_est_param_scale(mid), 0.5))
        scored.append((mid, score))
    scored.sort(key=lambda x: -x[1])
    # small jitter so ties/near-ties don't always go to the same model
    _random.seed()
    out = [m for m, _ in scored]
    _random.shuffle(out[:min(len(out), 3)]) if len(out) > 1 else None
    return out


def _est_param_scale(model_id: str) -> float:
    """Rough size class from the model name so we can prefer smaller models
    before learned data exists.  Returns an approximate 'billion params' figure."""
    import re
    s = model_id.lower()
    # MoE active params often encoded like 14b-a3b / 8b-a1b
    m = re.search(r"(\d+(?:\.\d+)?)\s*b\s*-?\s*a?\s*(\d+(?:\.\d+)?)\s*b", s)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*b", s)
    if m:
        return float(m.group(1))
    if "0.8b" in s or "0.6b" in s:
        return 0.8
    if "1.2b" in s:
        return 1.2
    if "3b" in s:
        return 3.0
    if "4b" in s:
        return 4.0
    if "8b" in s or "9b" in s:
        return 8.0
    if "12b" in s:
        return 12.0
    if "14b" in s:
        return 14.0
    if "20b" in s:
        return 20.0
    if "24b" in s:
        return 24.0
    if "35b" in s:
        return 35.0
    return 7.0

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
    """Route a chat request across the HOT fleet models only.

    Hard guarantees that keep the fleet stable:
      * We only ever send traffic to models that are positively HOT (resident in
        GPU) — never a cold-load attempt.  The loader thread is responsible for
        warming models, not the workers.
      * FLEET-ONLY: we never fall back to localhost / the host box (reserved for
        finetuning) and never to OpenRouter.
      * A (model, node) pair that errors is recorded as a SOFT miss on timeout
        (not a failure — the model may simply not be loaded yet) and as a HARD
        failure only on 4xx/5xx/connection errors.  After repeated hard failures
        it is suppressed until it succeeds once.  We never hammer-and-blacklist
        a node that was mid cold-load.
    """
    last_err: Optional[Exception] = None
    dispatch = _chat_ollama if LLM_BACKEND == "ollama" else _chat_openai
    deadline = time.time() + max(20, TIMEOUT)
    tried = 0
    for candidate in _candidate_models(model):
        if _cb_is_open(candidate):
            continue
        base_urls = fleet_base_urls_for(candidate)
        if not base_urls:
            # Model isn't hot anywhere right now — skip, don't cold-load.
            continue
        for base_url in base_urls:
            if time.time() > deadline:
                break
            if _is_pair_suppressed(candidate, base_url):
                continue
            t0 = time.time()
            try:
                out = dispatch(messages, candidate, json_mode, base_url=base_url,
                               _timeout_override=TIMEOUT)
                elapsed = time.time() - t0
                # Estimate TPS if we can recover token counts from usage.
                tps = None
                try:
                    # _chat_openai doesn't return usage; approximate via latency.
                    pass
                except Exception:
                    pass
                _cb_on_success(candidate)
                _track_node_latency(base_url, elapsed)
                _track_node_result(base_url, True)
                _mark_pair_ok(candidate, base_url)
                _record_perf(candidate, base_url, tps, elapsed, True)
                return out
            except Exception as e:
                elapsed = time.time() - t0
                _cb_on_failure(candidate)
                _track_node_latency(base_url, elapsed)
                _track_node_result(base_url, False)
                # Hard failure (real error) vs soft miss (timeout while cold).
                hard = not isinstance(e, requests.exceptions.Timeout)
                _mark_pair_failed(candidate, base_url, hard=hard)
                if hard:
                    _record_perf(candidate, base_url, None, elapsed, False)
                last_err = e
                tried += 1
                continue
    if last_err:
        raise last_err
    raise RuntimeError(
        "No HOT fleet models available to serve this request "
        "(fleet-only routing; host endpoint excluded)."
    )

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
    """Fleet-aware streaming.  Picks a HOT model on the fleet and streams from
    it.  Falls back to the configured model if it happens to be hot; never
    routes to localhost."""
    # Choose a model: explicit if hot, else best-ranked hot model.
    chosen = None
    if model:
        inv = _fleet_model_inventory()
        if model in inv:
            chosen = model
    if not chosen:
        ranked = _candidate_models(None)
        chosen = ranked[0] if ranked else (model or LLM_MODEL)
    urls = fleet_base_urls_for(chosen)
    base = urls[0] if urls else None
    if not base:
        yield {"event": "error", "data": {"error": "No hot fleet model available for streaming"}}
        return
    if LLM_BACKEND == "ollama":
        yield from _stream_ollama(messages, chosen, options)
    else:
        yield from _stream_openai_fleet(messages, chosen, base, options)

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


def _stream_openai_fleet(
    messages: List[Dict[str, str]], model: str, base_url: str,
    options: Optional[Dict[str, Any]] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Stream from a specific fleet base URL (never localhost)."""
    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if options:
        payload.update(options)
    url = f"{base_url.rstrip('/')}/chat/completions"
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


# ===========================================================================
# Autonomous fleet loader
# ---------------------------------------------------------------------------
# The loader is the ONLY component that triggers model loads.  Workers never
# cold-load.  The loader:
#   1. Discovers every model available across the fleet (from /v1/models).
#   2. Sorts them smallest-first (cheap models first, expect quality↑ as size↑).
#   3. For each online node, computes a capacity budget (learned from the KG or
#      a conservative default) and decides which models to KEEP hot there.
#   4. Loads missing target models ONE AT A TIME with a long timeout, writes
#      discovered specs + decisions back to the KG so knowledge accumulates.
#   5. Evicts the least-useful model on a node when at capacity (LRU by
#      quality×speed score).
# It runs on a background thread, started once per process.
# ===========================================================================

_LOADER_INTERVAL_S = float(os.getenv("LLM_LOADER_INTERVAL_S", "300"))
_LOADER_LOAD_TIMEOUT_S = float(os.getenv("LLM_LOADER_LOAD_TIMEOUT_S", "600"))
_LOADER_POLL_INTERVAL_S = float(os.getenv("LLM_LOADER_POLL_INTERVAL_S", "20"))
_LOADER_MAX_MODELS_PER_NODE = int(os.getenv("LLM_LOADER_MAX_MODELS_PER_NODE", "4"))
_LOADER_DISCOVER_TIMEOUT_S = float(os.getenv("LLM_LOADER_DISCOVER_TIMEOUT_S", "60"))
# Conservative per-node VRAM budget (GB) until the KG tells us otherwise.  The
# loader learns real footprints by loading and observing, then records them.
_LOADER_DEFAULT_VRAM_GB = float(os.getenv("LLM_LOADER_DEFAULT_VRAM_GB", "16"))
# Approx GB per 'billion params' for VRAM planning (quantised weights ~0.5GB/B
# for ~4-bit; we use a safe 0.9 to leave headroom for KV cache + context).
_GB_PER_B = 0.9
# Dedicated Neo4j driver for the loader so it can NEVER contend with / starve
# the request-serving shared driver (which would deadlock the API).
_loader_neo_client = None
_loader_neo_lock = threading.Lock()


def _loader_neo():
    """Return a loader-private Neo4j client (separate connection pool)."""
    global _loader_neo_client
    if _loader_neo_client is None:
        with _loader_neo_lock:
            if _loader_neo_client is None:
                _loader_neo_client = Neo4jClient()
                _loader_neo_client.shared = False
    return _loader_neo_client


def _loader_discover_models() -> List[str]:
    """All distinct model ids available on any online fleet node (on-disk)."""
    _refresh_fleet_nodes()
    seen: Dict[str, int] = {}
    for base in _fleet_nodes:
        ids = _probe_loaded_models(base)
        if not ids:
            continue
        for mid in ids:
            seen[mid] = seen.get(mid, 0) + 1
    return sorted(seen.keys(), key=lambda m: _est_param_scale(m))


def _loader_node_budget_gb(base_url: str) -> float:
    """Per-node VRAM budget in GB, learned from the KG when available."""
    node_id = base_url.rsplit("/v1", 1)[0].rsplit("/", 1)[-1]
    try:
        client = _loader_neo()
        with client._session() as s:
            rec = s.run(
                "MATCH (n:SwarmNode {node_id:$nid}) RETURN n.vram_budget_gb AS b",
                nid=node_id,
            ).single()
            if rec and rec.get("b"):
                return float(rec["b"])
    except Exception:
        pass
    return _LOADER_DEFAULT_VRAM_GB


def _loader_est_model_gb(model_id: str) -> float:
    """Estimated VRAM footprint in GB for planning (from name size class)."""
    return max(0.5, _est_param_scale(model_id) * _GB_PER_B)


def _loader_current_hot(base_url: str) -> List[str]:
    """Models currently confirmed hot on a node (cheap probe of target set)."""
    with _loader_lock:
        targets = list(_loader_target_models)
    if not targets:
        return []
    hot = []
    for mid in targets:
        try:
            r = requests.post(f"{base_url}/chat/completions", json={
                "model": mid, "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1, "temperature": 0, "stream": False,
            }, timeout=_COLD_START_TIMEOUT)
            if r.status_code == 200:
                body = r.json()
                if body.get("model") and mid in body.get("model", "") \
                        and body.get("choices", [{}])[0].get("message", {}).get("content"):
                    hot.append(mid)
        except Exception:
            pass
    return hot


def _native_models_url(base_url: str) -> str:
    """Map an OpenAI-style base URL (http://ip:1234/v1) to LM Studio's native
    model API (http://ip:1234/api/v1/models)."""
    return base_url.replace("/v1", "/api/v1") + "/models"


def _loader_native_load(base_url: str, model_id: str) -> Optional[str]:
    """Trigger a load via LM Studio's native API (POST .../models/load).

    Returns the instance_id on success (needed for a later unload) or None.
    This is the authoritative load path — chat/completions only *implicitly*
    triggers a load and is unreliable for some model keys (400/500)."""
    try:
        url = _native_models_url(base_url) + "/load"
        r = requests.post(url, json={"model": model_id},
                          timeout=_LOADER_LOAD_TIMEOUT_S)
        if r.status_code in (200, 201):
            try:
                return r.json().get("instance_id")
            except Exception:
                return "loaded"
        return None
    except Exception:
        return None


def _loader_native_unload(base_url: str, instance_id: str) -> bool:
    """Unload a previously-loaded model instance via the native API.  Accepts
    either a real instance_id (from a loader load) or a model id (for manually
    loaded instances that report no instance_id)."""
    try:
        url = _native_models_url(base_url) + "/unload"
        r = requests.post(url, json={"instance_id": instance_id, "id": instance_id},
                          timeout=_LOADER_LOAD_TIMEOUT_S)
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def _loaded_instance_ids_native(base_url: str, model_id: str) -> List[str]:
    """Best-effort lookup of native instance_ids for a loaded model on a node."""
    try:
        url = _native_models_url(base_url)
        r = requests.get(url, timeout=_COLD_START_TIMEOUT)
        if r.status_code != 200:
            return []
        for m in r.json().get("models", []):
            if m.get("key") == model_id or m.get("id") == model_id:
                return [i.get("instance_id") for i in (m.get("loaded_instances") or [])
                        if i.get("instance_id")]
    except Exception:
        pass
    return []


def _snapshot_node_loaded(base_url: str) -> List[Dict[str, Any]]:
    """Snapshot everything actually resident on a node right now, with the real
    LM Studio load config for each instance.  Returns a list of
    {model_id, instance_id, config, owner} where owner is "assistx" if we loaded
    it (instance_id tracked) or "user" if the operator loaded it manually.

    This is what lets the loader MIRROR the operator's layout: we learn the
    exact config (context length, MTP draft, parallel, …) of a manual load and
    reuse it later, and we never remove a "user"-owned instance."""
    out: List[Dict[str, Any]] = []
    try:
        url = _native_models_url(base_url)
        r = requests.get(url, timeout=_COLD_START_TIMEOUT)
        if r.status_code != 200:
            return out
        for m in r.json().get("models", []):
            key = m.get("key") or m.get("id")
            if not key:
                continue
            for inst in (m.get("loaded_instances") or []):
                # LM Studio usually reports instance_id, but manually-loaded
                # instances may only expose the model `id`.  Use whichever is
                # present as the unload handle.
                iid = inst.get("instance_id") or inst.get("id")
                if not iid:
                    continue
                owner = "assistx" if _loader_instance_ids.get((base_url, key)) == iid else "user"
                out.append({
                    "model_id": key,
                    "instance_id": iid,
                    "config": dict(inst.get("config") or {}),
                    "owner": owner,
                })
    except Exception:
        pass
    return out


def _loader_native_load(base_url: str, model_id: str,
                        config: Optional[dict] = None) -> Optional[str]:
    """Trigger a load via LM Studio's native API (POST .../models/load).

    Returns the instance_id on success (needed for a later unload) or None.
    NOTE: LM Studio's load endpoint only accepts {"model": ...} — it rejects any
    config key (400 "Unrecognized key(s)").  Per-model config (context length,
    MTP draft, …) lives in LM Studio's saved preset for that model and is applied
    automatically on load, so we do NOT pass `config` here.  We still *track* the
    operator's config (see _loader_node_configs / _loader_user_configs) for
    mirroring + UI visibility.  This is the authoritative load path — not
    chat/completions, which only implicitly triggers a load and is unreliable
    for some model keys (400/500)."""
    try:
        url = _native_models_url(base_url) + "/load"
        r = requests.post(url, json={"model": model_id},
                          timeout=_LOADER_LOAD_TIMEOUT_S)
        if r.status_code in (200, 201):
            try:
                return r.json().get("instance_id") or model_id
            except Exception:
                return model_id
        return None
    except Exception:
        return None


def _loader_warmup(base_url: str, model_id: str) -> Dict[str, Any]:
    """After a model is loaded, do ONE real completion to learn its perf on this
    node (tps, latency) and record it to the KG so routing can rank it.

    The warmup also confirms the model actually answers (not just that LM Studio
    accepted the load request).  Returns the observed metrics."""
    t0 = time.time()
    try:
        r = requests.post(f"{base_url}/chat/completions", json={
            "model": model_id,
            "messages": [{"role": "user",
                          "content": "Reply with exactly: OK."}],
            "max_tokens": 24, "temperature": 0, "stream": False,
        }, timeout=_LOADER_LOAD_TIMEOUT_S)
        latency = time.time() - t0
        ok = (r.status_code == 200 and r.json().get("choices", [{}])[0]
              .get("message", {}).get("content", "").strip())
        # Estimate tokens/sec from usage if available, else fall back to latency.
        tps = None
        try:
            usage = r.json().get("usage", {})
            comp = usage.get("completion_tokens") or 0
            if comp and latency > 0:
                tps = comp / latency
        except Exception:
            pass
        _record_perf(model_id, base_url, tps, latency, bool(ok), tokens=0)
        return {"ok": bool(ok), "tps": tps, "latency_s": latency}
    except Exception:
        _record_perf(model_id, base_url, None, None, False, tokens=0)
        return {"ok": False, "tps": None, "latency_s": None}


def _loader_wait_hot(base_url: str, model_id: str, timeout_s: float) -> bool:
    """Poll until `model_id` actually answers on `base_url` (it is resident),
    or until `timeout_s` elapses.  Uses a relaxed probe (no hot-threshold
    timing requirement) and paces requests so we don't spam the node while it
    is loading weights.

    A *fast* error response (4xx/5xx in well under the connect timeout) means
    this node cannot serve the model at all — we stop waiting immediately
    rather than burning the whole timeout.  Only a slow/hanging response (a
    real cold-load in progress) keeps us polling, since that is LM Studio
    paging weights into VRAM, which can take minutes."""
    deadline = time.time() + timeout_s
    fast_fail = _COLD_START_TIMEOUT * 0.5
    while time.time() < deadline:
        t0 = time.time()
        try:
            r = requests.post(f"{base_url}/chat/completions", json={
                "model": model_id,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1, "temperature": 0, "stream": False,
            }, timeout=_COLD_START_TIMEOUT)
            if r.status_code == 200:
                body = r.json()
                if body.get("model") and model_id in body.get("model", "") \
                        and body.get("choices", [{}])[0].get("message", {}).get("content"):
                    return True
            # Definitive fast failure: node rejects the model, don't keep waiting.
            if (time.time() - t0) < fast_fail:
                return False
        except Exception:
            # Timeout/connection slowness => likely still loading; keep polling.
            pass
        # Pace the poll + publish progress so the status endpoint shows we are
        # alive and patiently waiting for weights to page into VRAM.
        with _loader_lock:
            _loader_state["last_action"] = (
                f"waiting for {model_id} on {base_url.replace('http://','').replace(':1234/v1','')} "
                f"({int(deadline - time.time())}s left)"
            )
        time.sleep(_LOADER_POLL_INTERVAL_S)
    return False


def _loader_load(base_url: str, model_id: str,
                 config: Optional[dict] = None) -> bool:
    """Deliberately load `model_id` on `base_url` via LM Studio's native API,
    then WAIT for it to actually become resident before returning.

    This is the ONLY place loads are triggered.  We use the native
    ``/api/v1/models/load`` endpoint (not chat/completions, which only
    implicitly triggers a load and is unreliable for some model keys).  We never
    kill a load mid-flight (generous timeout), and we do not move on until the
    model answers a real probe — so the routing hot-inventory and per-node plan
    reflect reality.  The returned instance_id is cached (owner == "assistx")
    so the operator (or a future swap) can unload it later.  `config` carries
    the LM Studio load config (context length, MTP draft, …) for this node.
    After it is hot we run a single warmup completion to learn its perf for
    routing."""
    iid = _loader_native_load(base_url, model_id, config=config)
    if not iid:
        _loader_write_kg(base_url, model_id, False)
        return False
    with _loader_lock:
        _loader_instance_ids[(base_url, model_id)] = iid
        _loader_owned[(base_url, model_id)] = "assistx"
    _loader_write_kg(base_url, model_id, True)
    # Wait for it to be genuinely resident, then warm it up + learn perf.
    hot = _loader_wait_hot(base_url, model_id, _LOADER_LOAD_TIMEOUT_S)
    if hot:
        _loader_warmup(base_url, model_id)
    return hot


def _loader_write_kg(base_url: str, model_id: str, ok: bool) -> None:
    """Persist loader decisions + discovered specs to the KG (not a JSON file).

    Writes a :Model node (so the graph knows this model exists + its size class)
    and stamps the :ModelEndpoint with the last loader result.  Best-effort,
    uses the loader-private driver so it never blocks request threads."""
    try:
        client = _loader_neo()
        with client._session() as s:
            s.run("""
                MERGE (m:Model {model_id:$mid})
                ON CREATE SET m.size_class=$size, m.loader_seen=true,
                              m.created_at_ts=$ts
                ON MATCH SET m.loader_seen=true, m.size_class=$size
            """, mid=model_id, size=_est_param_scale(model_id), ts=int(time.time()*1000))
            s.run("""
                MATCH (e:ModelEndpoint {base_url:$url})
                SET e.last_loader_ts=$ts, e.last_loader_ok=$ok
            """, url=base_url, ts=int(time.time()*1000), ok=ok)
    except Exception:
        pass


def _fleet_loader_loop() -> None:
    """Background loop: keep the fleet's curated model set hot, stably."""
    with _loader_lock:
        _loader_state["running"] = True
    while True:
        try:
            _refresh_fleet_nodes()
            nodes = list(_fleet_nodes)
            if not nodes:
                with _loader_lock:
                    _loader_state["last_action"] = "no fleet nodes reachable"
                time.sleep(_LOADER_INTERVAL_S)
                continue

            all_models = _loader_discover_models()
            with _loader_lock:
                _loader_state["discovered_models"] = all_models
                _loader_state["cycle"] += 1
                cycle = _loader_state["cycle"]

            # --- Build the desired layout ----------------------------------
            # The operator is in control.  The target set on each node is the
            # UNION of:
            #   * the pinned wishlist (explicit operator choice),
            #   * models the operator has ALREADY loaded manually on a node
            #     (snapshotted + mirrored, so we converge to your layout),
            #   * any demand a subsystem (e.g. portfolio trader) requested.
            # We never remove a model the operator loaded manually — we only
            # ever unload models assistx itself loaded that are no longer
            # wanted.  And we patiently wait for cold loads to page in.
            with _loader_lock:
                pinned = set(_loader_pinned_models)
                demand = set(_loader_demand)

            # Snapshot every node: what is resident, with its real config + owner.
            import concurrent.futures as _cf
            node_loaded: Dict[str, List[Dict[str, Any]]] = {}
            with _cf.ThreadPoolExecutor(max_workers=min(len(nodes) or 1, 8)) as pool:
                for base in nodes:
                    node_loaded[base] = _snapshot_node_loaded(base)

            # Fold manual loads into the mirrored layout + remember their config.
            mirrored: Dict[tuple, dict] = {}
            for base, snap in node_loaded.items():
                for entry in snap:
                    key = (base, entry["model_id"])
                    if entry["owner"] == "user":
                        mirrored[key] = entry["config"]
                        with _loader_lock:
                            _loader_user_configs[key] = entry["config"]
                            _loader_owned[key] = "user"

            target = pinned | set(m for (_, m) in mirrored.keys()) | demand
            # Restrict to models that actually exist somewhere on the fleet.
            target = {m for m in target if m in all_models}

            if not target:
                with _loader_lock:
                    owners = {f"{b}|{m}": o for (b, m), o in _loader_owned.items()}
                    _loader_state["last_action"] = (
                        f"cycle {cycle}: discovered {len(all_models)} models, "
                        f"mirroring operator layout, no new loads needed"
                    )
                    _loader_state["last_run_ts"] = time.time()
                    _loader_state["owners"] = owners
                _loader_wake.wait(timeout=_LOADER_INTERVAL_S)
                _loader_wake.clear()
                continue

            per_node_results: Dict[str, Dict[str, Any]] = {}

            def _plan_and_load(base: str) -> None:
                budget = _loader_node_budget_gb(base)
                snap = {e["model_id"]: e for e in node_loaded.get(base, [])}
                resident = set(snap.keys())
                # Targets are fleet-wide model-id strings; a node only takes a
                # target if that model exists on the node (all_models is the
                # union, so check per-node presence via the snapshot instead).
                node_models = set()
                try:
                    r = requests.get(_native_models_url(base), timeout=_COLD_START_TIMEOUT)
                    if r.status_code == 200:
                        node_models = {m.get("key") or m.get("id")
                                       for m in r.json().get("models", []) if m.get("key") or m.get("id")}
                except Exception:
                    pass
                str_targets = {m for m in target if m in node_models}
                # Budget across the node, but PRIORITIZE the operator's explicit
                # pins / subsystem demand over mirrored user loads — the operator
                # chose those and they must not be starved by the cap.
                with _loader_lock:
                    pinned_set = set(_loader_pinned_models)
                    demand_set = set(_loader_demand)
                priority = sorted(str_targets & (pinned_set | demand_set), key=_est_param_scale)
                mirror = sorted(str_targets - (pinned_set | demand_set), key=_est_param_scale)
                plan = [m for m in resident if m in str_targets]
                est_used = sum(_loader_est_model_gb(m) for m in plan)
                for m in priority + mirror:
                    if len(plan) >= _LOADER_MAX_MODELS_PER_NODE:
                        break
                    if m in plan:
                        continue
                    need = _loader_est_model_gb(m)
                    if est_used + need <= budget:
                        plan.append(m)
                        est_used += need
                results = {}
                actions = []
                for m in plan:
                    if m in resident:
                        continue
                    # Pick the config: explicit per-node config > snapshotted
                    # user config > None (LM Studio default).
                    cfg = _loader_node_configs.get((base, m)) \
                        or _loader_user_configs.get((base, m))
                    actions.append(f"load {m}")
                    ok = _loader_load(base, m, config=cfg)
                    results[m] = ok
                    if ok:
                        with _loader_lock:
                            _loader_user_configs.pop((base, m), None)
                # Remove only assistx-owned models that are no longer wanted.
                for m, entry in snap.items():
                    if entry["owner"] != "assistx":
                        continue
                    if m in str_targets:
                        continue
                    actions.append(f"unload {m}")
                    if _loader_native_unload(base, entry["instance_id"]):
                        with _loader_lock:
                            _loader_instance_ids.pop((base, m), None)
                            _loader_owned.pop((base, m), None)
                        results[m] = "unloaded"
                current_hot = _loader_current_hot(base)
                # Capture each resident model's active LM Studio config (context
                # length, MTP draft, parallel, …) for the UI — this is what the
                # operator set in LM Studio and assistx mirrors.
                configs = {}
                for mid, entry in snap.items():
                    cfg = entry.get("config") or {}
                    ctx = cfg.get("context_length")
                    mtp = cfg.get("speculative_draft_mtp")
                    draft = cfg.get("speculative_draft_model")
                    bits = []
                    if ctx:
                        bits.append("ctx " + (str(ctx) if ctx < 1000 else (str(ctx // 1000) + "k")))
                    if mtp:
                        bits.append("MTP")
                    if draft:
                        bits.append("draft:" + str(draft).split("/")[-1])
                    configs[mid] = " ".join(bits)
                per_node_results[base] = {
                    "budget_gb": budget, "hot": current_hot, "plan": plan,
                    "actions": actions, "loaded_ok": results, "configs": configs,
                    "owners": {m: e["owner"] for m, e in snap.items()},
                }
                with _loader_lock:
                    _loader_state["per_node"][base] = per_node_results[base]
                    _loader_target_models.update(plan)

            with _loader_lock:
                _loader_state["last_action"] = (
                    f"cycle {cycle}: converging {len(target)} targets across "
                    f"{len(nodes)} nodes (mirroring operator layout)"
                )
            with _cf.ThreadPoolExecutor(max_workers=min(len(nodes) or 1, 8)) as pool:
                list(pool.map(_plan_and_load, nodes))

            with _loader_lock:
                owners = {f"{b}|{m}": o for (b, m), o in _loader_owned.items()}
                _loader_state["last_run_ts"] = time.time()
                _loader_state["owners"] = owners
                _loader_state["last_action"] = (
                    f"cycle {cycle}: converged {len(target)} targets across "
                    f"{len(nodes)} nodes"
                )
        except Exception as exc:
            with _loader_lock:
                _loader_state["last_action"] = f"loader error: {exc}"
            try:
                import traceback as _tb
                _tb.print_exc()
            except Exception:
                pass
        # Sleep until the next interval, or wake early if the operator pins a
        # new wishlist / triggers a load (immediate control, not delayed).
        _loader_wake.wait(timeout=_LOADER_INTERVAL_S)
        _loader_wake.clear()


_loader_thread: Optional[threading.Thread] = None
_loader_wake = threading.Event()


def start_fleet_loader() -> None:
    """Start the autonomous loader thread once (idempotent)."""
    global _loader_thread
    if _loader_thread and _loader_thread.is_alive():
        return
    if os.getenv("LLM_LOADER_DISABLE") == "1":
        return
    _loader_thread = threading.Thread(target=_fleet_loader_loop, name="fleet-loader", daemon=True)
    _loader_thread.start()


def get_loader_state() -> Dict[str, Any]:
    if _loader_lock.acquire(timeout=2):
        try:
            return dict(_loader_state)
        finally:
            _loader_lock.release()
    return {"running": False, "stale": True}


def set_loader_wishlist(models: List[str]) -> None:
    """Operator-driven control: pin the loader's wishlist to exactly `models`
    (must be models discoverable on the fleet).  Pass an empty list to return
    control to mirroring the operator's manual layout.  The operator decides
    what loads — not the code."""
    global _loader_pinned_models
    _loader_pinned_models = set(models or [])
    with _loader_lock:
        _loader_state["pinned"] = sorted(_loader_pinned_models)
        _loader_state["last_action"] = (
            f"operator pinned {len(_loader_pinned_models)} models"
            if _loader_pinned_models else "operator released control (mirroring layout)"
        )
    _loader_wake.set()


def set_node_config(base_url: str, model_id: str, config: dict) -> None:
    """Assign a per-node load config (context length, MTP draft, parallel, …)
    for `model_id` on `base_url`.  Applied on the next load of that model here.
    Pass None to clear and fall back to the snapshotted user config / default."""
    global _loader_node_configs
    if config is None:
        _loader_node_configs.pop((base_url, model_id), None)
    else:
        _loader_node_configs[(base_url, model_id)] = dict(config)
    _loader_wake.set()


def request_model(model_id: str) -> None:
    """Subsystem demand: ask the loader to keep `model_id` resident somewhere on
    the fleet (e.g. the portfolio trader needs it).  The loader will converge to
    include it; clears automatically once it is hot on a node."""
    global _loader_demand
    _loader_demand.add(model_id)
    _loader_wake.set()


def release_model(model_id: str) -> None:
    """Drop a prior demand request for `model_id`."""
    global _loader_demand
    _loader_demand.discard(model_id)
    _loader_wake.set()


def load_model_on_node(base_url: str, model_id: str,
                       config: Optional[dict] = None) -> Dict[str, Any]:
    """Synchronous operator load: load `model_id` on a specific node now and wait
    for it to become hot.  `config` optionally applies a load config.  Returns
    {ok, hot, instance_id}.  Operator loads are tracked as owner="user" so the
    loader mirrors (never removes) them."""
    iid = _loader_native_load(base_url, model_id, config=config)
    if not iid:
        return {"ok": False, "hot": False, "instance_id": None}
    with _loader_lock:
        _loader_instance_ids[(base_url, model_id)] = iid
        _loader_owned[(base_url, model_id)] = "user"
        _loader_target_models.add(model_id)
    hot = _loader_wait_hot(base_url, model_id, _LOADER_LOAD_TIMEOUT_S)
    if hot:
        _loader_warmup(base_url, model_id)
    return {"ok": True, "hot": hot, "instance_id": iid}


def unload_model_on_node(base_url: str, model_id: str) -> Dict[str, Any]:
    """Operator unload: unload `model_id` from a specific node via its captured
    native instance_id (or discovered from the node).  Returns {ok}.  This is the
    operator's explicit removal — the loader will then mirror the new layout."""
    iid = _loader_instance_ids.get((base_url, model_id))
    if not iid:
        # Fall back to discovering the instance_id from the node.
        iid = (_loaded_instance_ids_native(base_url, model_id) or [None])[0]
    if not iid:
        return {"ok": False, "reason": "no instance_id (not loaded by loader?)"}
    ok = _loader_native_unload(base_url, iid)
    if ok:
        with _loader_lock:
            _loader_instance_ids.pop((base_url, model_id), None)
            _loader_owned.pop((base_url, model_id), None)
            _loader_target_models.discard(model_id)
    return {"ok": ok}
