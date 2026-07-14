"""Dynamic, self-tuning fleet registry + routing.

The swarm's models change faster than any config file. So this module does NOT
rely on static model lists. Instead it:

  * discovers every live node/model from the router's ``/v1/models`` *aggressively*
    (a background refresh thread re-checks every few seconds), so nodes appearing
    or sleeping are picked up automatically;
  * *measures* each model's real behaviour -- throughput (tok/s) and success rate,
    learned both from a one-shot probe on first sight and from every real call;
  * *routes by task need*: a task declares how latency-tolerant and quality-hungry
    it is, and ``select`` scores each candidate node/model on capability x measured
    speed x quality x observed success. Slow-but-smart models (e.g. a Qwen MoE on
    destroyer) are chosen for hard reasoning; slow nodes (e.g. beelink's 9B at
    2-5 tok/s) are used for background/summarisation but never for an interactive
    session a human is waiting on.

Node-prefixed ids (``lmstudio-<node>.<model>``) are returned so the router always
routes to the intended machine.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import socket
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

ROUTER_MODELS_URL = os.getenv(
    "ASSISTX_ROUTER_MODELS_URL", "http://host.docker.internal:8088/v1/models"
)
ROUTER_CHAT_URL = os.getenv(
    "ASSISTX_ROUTER_CHAT_URL", "http://host.docker.internal:8088/v1/chat/completions"
)
# Aggressive: the fleet changes constantly, so re-check often.
REGISTRY_TTL = float(os.getenv("FLEET_REGISTRY_TTL", "5"))
PROBE_TOKENS = int(os.getenv("FLEET_PROBE_TOKENS", "24"))
PROBE_COOLDOWN = float(os.getenv("FLEET_PROBE_COOLDOWN", "300"))  # re-probe every 5 min
PROBE_PER_CYCLE = int(os.getenv("FLEET_PROBE_PER_CYCLE", "3"))   # avoid probe storms

# Failure-aware routing: nodes that start failing are demoted for a while (so we
# stop hammering a sleeping/crashed machine) but automatically recovered once they
# answer again -- the fleet heals itself.
FAIL_DEMOTE = int(os.getenv("FLEET_FAIL_DEMOTE", "3"))       # consecutive fails -> avoid
DOWN_PENALTY = float(os.getenv("FLEET_DOWN_PENALTY", "120")) # seconds to avoid after demotion
# Load-spreading: among candidates within this score of the best, pick the one
# used least recently -- so the whole fleet shares work like worker bees instead
# of one node being hammered while others idle.
SPREAD_EPS = float(os.getenv("FLEET_SPREAD_EPS", "0.4"))
# Credit unmeasured models with a neutral speed so they get tried (exploration)
# rather than being permanently shunned for looking slow.
ASSUMED_TOK_S = float(os.getenv("FLEET_ASSUMED_TOK_S", "12"))

# Utilisation pressure: an idle model/machine is a *wasted* worker bee. Instead
# of a flat capped boost (which is applied equally to every quiet node and so
# never reorders them), we use RELATIVE pressure: each candidate's idle time is
# compared to the AVERAGE idle time of the pool. A node that has been quiet
# longer than its peers earns a positive pull; one that was just used earns a
# penalty. This actively drags the most-idle capable node back into the rotation
# -- the negative consequence of under-utilisation -- while the capability filter
# (min_params) and base fit still stop a grossly unfit model from winning.
UTIL_TAU = float(os.getenv("FLEET_UTIL_TAU", "120"))      # idle window for pressure
INFLOW_PENALTY = float(os.getenv("FLEET_INFLOW_PENALTY", "0.25"))  # per in-flight task
REL_PRESSURE = float(os.getenv("FLEET_REL_PRESSURE", "0.9"))      # pressure band (+/-)

# Watchdog: nodes that actually crash (not just sleep) need a poke, not only
# avoidance. When a node's models have tripped the failure demotion (3 strikes ->
# down_until), the watchdog tries to bring it back: first a user-supplied wake
# command (FLEET_WAKE_CMD, "{node}" substituted), then a Wake-on-LAN magic packet
# to the MAC in FLEET_WAKE_MAP. Both are optional; without them the watchdog just
# logs the down node so the gap in "every node utilised" is visible. A per-node
# cooldown stops us spamming a dead box.
WATCHDOG_INTERVAL = int(os.getenv("FLEET_WATCHDOG_INTERVAL", "30"))  # secs between sweeps
WAKE_COOLDOWN = float(os.getenv("FLEET_WAKE_COOLDOWN", "300"))      # min secs between wake tries
WAKE_BROADCAST = os.getenv("FLEET_WAKE_BROADCAST", "255.255.255.255")
WAKE_PORT = int(os.getenv("FLEET_WAKE_PORT", "9"))
WAKE_MAP = {}
for _wpair in os.getenv("FLEET_WAKE_MAP", "").split(","):
    if "=" in _wpair:
        _wn, _wm = _wpair.split("=", 1)
        _wn, _wm = _wn.strip(), _wm.strip()
        if _wn and _wm:
            WAKE_MAP[_wn] = _wm
WAKE_CMD = os.getenv("FLEET_WAKE_CMD", "").strip()  # template, {node} substituted
_last_wake: Dict[str, float] = {}

# Per-node self-task concurrency cap. Background self-tasks are the only workload
# we fire in bursts (HERMES_SELFTASK_CONCURRENCY at once); a single small node must
# never run more than this many at once or it overloads and crashes (which is what
# took the laptops down). The relative pressure already spreads work, but this is
# the hard backstop. Default 1 (small laptops), with a size-aware bump for big
# nodes (they can take the load) and optional per-node overrides.
NODE_SELFTASK_CAP = int(os.getenv("FLEET_NODE_SELFTASK_CAP", "1"))
NODE_SELFTASK_CAP_MAP = {}  # node -> cap override
for _cpair in os.getenv("FLEET_NODE_SELFTASK_CAP_MAP", "").split(","):
    if "=" in _cpair:
        _cn, _cv = _cpair.split("=", 1)
        _cn, _cv = _cn.strip(), _cv.strip()
        if _cn and _cv.isdigit():
            NODE_SELFTASK_CAP_MAP[_cn] = int(_cv)
_node_selftask_inflight: Dict[str, int] = {}

_lock = threading.Lock()
_nodes: dict = {}          # node -> {"models": {bare: full_id}}
_model_map: dict = {}      # bare model -> [(node, full_id, bare), ...]
_last_refresh = 0.0
_lru: dict = {}            # full_id -> last used timestamp (load spreading)
_node_last_used: dict = {} # node -> last used timestamp (whole-machine idleness)
_inflight: dict = {}       # full_id -> in-flight task count (transient, not persisted)
_node_inflight: dict = {}  # node -> in-flight task count (transient)
_perf: dict = {}           # full_id -> {tok_s, latency, success, n, first_seen, last_used,
                           #            calls_by_tier, fail_streak, down_until}
_decisions: list = []      # ring buffer of recent routing choices (for intuition)
_refresh_thread = None
_refresh_started = False
_metrics_thread = None
_metrics_started = False

# Metrics accumulate across restarts so the fleet 'learns' over time.
METRICS_PATH = os.getenv("ASSISTX_FLEET_METRICS_PATH",
                         "/root/knowledge/.metrics/fleet.json")
_METRICS_SAVE_INTERVAL = float(os.getenv("FLEET_METRICS_SAVE_INTERVAL", "30"))
_last_metrics_save = 0.0
_EMA_ALPHA = 0.3          # smoothing for tok/s + latency
_DECISION_CAP = 300


def _load_metrics() -> None:
    global _perf, _decisions
    try:
        with open(METRICS_PATH, "r", errors="ignore") as fh:
            d = json.load(fh)
        _perf.update({k: v for k, v in d.get("perf", {}).items()})
        _decisions.extend(d.get("decisions", [])[-_DECISION_CAP:])
    except (OSError, ValueError):
        pass


def _save_metrics() -> None:
    global _last_metrics_save
    _last_metrics_save = time.time()
    try:
        os.makedirs(os.path.dirname(METRICS_PATH), exist_ok=True)
        with open(METRICS_PATH, "w") as fh:
            json.dump({"perf": _perf, "decisions": _decisions[-_DECISION_CAP:]}, fh)
    except OSError:
        pass


_load_metrics()

# ---------------------------------------------------------------------------
# Model-capability heuristics (no per-model static config -- derived from name)
# ---------------------------------------------------------------------------
_PARAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(b|m)", re.I)


def _params_of(model: str) -> float:
    """Approximate model size in billions of params from its name."""
    best = 0.0
    for num, unit in _PARAM_RE.findall(model):
        v = float(num) * (0.001 if unit.lower() == "m" else 1.0)
        if v > best:
            best = v
    return best or 3.0


def _is_moe(model: str) -> bool:
    m = model.lower()
    return ("moe" in m) or ("mixture" in m)


def _is_toolish(model: str) -> bool:
    m = model.lower()
    return any(k in m for k in ("tool", "vibethinker", "hermes", "instruct", "chat", "qwen"))


def quality_score(model: str) -> float:
    """0..1 — how 'smart' a model is likely to be (size + MoE bonus)."""
    p = _params_of(model)
    q = min(p / 35.0, 1.0)
    if _is_moe(model):
        q = min(q + 0.2, 1.0)
    return q


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover(force: bool = False) -> None:
    """Refresh node/model maps from the router. Cached via TTL; the background
    thread keeps it warm so callers rarely block."""
    global _nodes, _model_map, _last_refresh
    now = time.time()
    if not force and _nodes and now - _last_refresh < REGISTRY_TTL:
        return
    with _lock:
        if not force and _nodes and now - _last_refresh < REGISTRY_TTL:
            return
    # Fetch OUTSIDE the lock so a slow/unreachable router never blocks the whole
    # fleet module (selection, metrics, background loop all contend on _lock).
    try:
        data = requests.get(ROUTER_MODELS_URL, timeout=10).json()
    except Exception:
        return  # keep previous registry on failure
    nodes: dict = {}
    model_map: dict = {}
    for entry in data.get("data", []):
        mid = entry.get("id", "")
        if not mid.startswith("lmstudio-"):
            continue
        rest = mid[len("lmstudio-"):]
        node, _, model = rest.partition(".")  # first dot: node names have none
        if not node or not model:
            continue
        full = mid
        nodes.setdefault(node, {"models": {}})
        nodes[node]["models"][model] = full
        # The router sometimes lists the same id twice; dedupe so load
        # spreading isn't biased toward duplicated models.
        existing = model_map.setdefault(model, [])
        if (node, full, model) not in existing:
            existing.append((node, full, model))
    with _lock:
        _nodes, _model_map, _last_refresh = nodes, model_map, time.time()


def _ensure_refresh_thread() -> None:
    global _refresh_thread, _refresh_started
    if _refresh_started:
        return
    with _lock:
        if _refresh_started:
            return
        _refresh_started = True
    _start_metrics_server()

    def _loop() -> None:
        cyc = 0
        while True:
            try:
                discover(force=True)
                _probe_stale()
                cyc += 1
                # Watchdog: detect crashed nodes and try to wake them (every
                # WATCHDOG_INTERVAL seconds). Runs in the same background thread.
                if cyc % max(1, int(WATCHDOG_INTERVAL / REGISTRY_TTL)) == 0:
                    _watchdog_sweep()
                if cyc % 12 == 0:  # ~ every minute: persist + surface intuition
                    _save_metrics()
                    log_summary()
            except Exception:
                pass
            time.sleep(REGISTRY_TTL)

    _refresh_thread = threading.Thread(target=_loop, daemon=True)
    _refresh_thread.start()


def start() -> None:
    """Public entry point: kick off the background loop NOW (discovery, probing,
    metrics server, periodic logging). Idempotent -- safe to call from app start.
    """
    _ensure_refresh_thread()
    try:
        discover(force=True)
        _probe_stale()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Performance learning
# ---------------------------------------------------------------------------
def record_outcome(full_id: str, elapsed: float, n_tokens: Optional[int] = None,
                   success: bool = True, tier: Optional[str] = None) -> None:
    """Feed a real call's result back so routing improves over time.

    Maintains EMA tok/s + latency, a success rate, total call count, per-tier
    call counts and first/last-seen -- all persisted so intuition accumulates
    across restarts.
    """
    if not full_id:
        return
    now = time.time()
    with _lock:
        p = _perf.get(full_id)
        if p is None:
            p = {"tok_s": 0.0, "latency": 0.0, "success": 1.0, "n": 0,
                 "first_seen": now, "last_used": now, "calls_by_tier": {},
                 "last_probe": 0.0, "fail_streak": 0, "down_until": 0.0}
            _perf[full_id] = p
        p["n"] = p.get("n", 0) + 1
        p["last_used"] = now
        a = _EMA_ALPHA
        p["latency"] = p.get("latency", 0.0) * (1 - a) + elapsed * a
        if n_tokens:
            rate = n_tokens / elapsed if elapsed > 0 else 0.0
            p["tok_s"] = p.get("tok_s", 0.0) * (1 - a) + rate * a
        p["success"] = p.get("success", 1.0) * (1 - a) + (1.0 if success else 0.0) * a
        # Failure-aware: a string of failures means the node is likely asleep or
        # crashed, so we avoid it for a bit; a single success heals it.
        if success:
            p["fail_streak"] = 0
            p["down_until"] = 0.0
        else:
            p["fail_streak"] = p.get("fail_streak", 0) + 1
            if p["fail_streak"] >= FAIL_DEMOTE:
                p["down_until"] = now + DOWN_PENALTY
        if tier:
            tiers = p.setdefault("calls_by_tier", {})
            tiers[tier] = tiers.get(tier, 0) + 1
        if now - _last_metrics_save >= _METRICS_SAVE_INTERVAL:
            _save_metrics()
    # Release the lock first: _mark_done takes _lock itself (non-reentrant), so
    # calling it inside the lock above would deadlock the whole fleet module.
    _mark_done(full_id)


def _record_choice(full_id: str, task: dict, score: float) -> None:
    """Log a routing decision so we can see *why* the fleet chose what it did."""
    _decisions.append({
        "ts": time.time(),
        "model": full_id,
        "task": {k: task.get(k) for k in ("min_params", "latency_tolerance",
                                          "quality_need", "toolish")},
        "score": round(score, 4),
    })
    if len(_decisions) > _DECISION_CAP * 2:
        del _decisions[:-_DECISION_CAP]


# ---------------------------------------------------------------------------
# Watchdog: detect crashed nodes and try to wake them (not just avoid them)
# ---------------------------------------------------------------------------
def _send_wol(mac: str, broadcast: str = WAKE_BROADCAST, port: int = WAKE_PORT) -> bool:
    """Send a Wake-on-LAN magic packet to ``mac`` via UDP broadcast. Self-contained
    (no external dependency). Returns True if the packet was sent."""
    try:
        addr = [int(x, 16) for x in mac.replace("-", ":").split(":")]
        if len(addr) != 6:
            return False
        payload = bytes([0xFF] * 6 + addr * 16)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(payload, (broadcast, port))
        s.close()
        return True
    except Exception as exc:  # bad MAC, no broadcast route, etc.
        logger.debug("Watchdog: WoL send failed for %s: %s", mac, exc)
        return False


def wake_node(node: str) -> bool:
    """Best-effort attempt to bring a crashed node back. Returns True if a wake
    action was issued (not whether it succeeded -- the node has to actually boot).
    Respects WAKE_COOLDOWN so we don't hammer a dead box every sweep."""
    now = time.time()
    if now - _last_wake.get(node, 0.0) < WAKE_COOLDOWN:
        return False
    _last_wake[node] = now
    if WAKE_CMD:
        try:
            subprocess.run(
                WAKE_CMD.format(node=node), shell=True, timeout=30,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            logger.info("Watchdog: sent wake command for node %s", node)
            return True
        except Exception as exc:
            logger.warning("Watchdog: wake command failed for %s: %s", node, exc)
    mac = WAKE_MAP.get(node)
    if mac:
        if _send_wol(mac):
            logger.info("Watchdog: sent WoL magic packet to %s (%s)", node, mac)
            return True
        logger.warning("Watchdog: WoL send failed for %s", node)
    logger.info(
        "Watchdog: node %s is down; no wake mechanism configured "
        "(set FLEET_WAKE_MAP='%s=MA:CA:DD:RS' and/or FLEET_WAKE_CMD)", node, node
    )
    return False


def node_health() -> Dict[str, dict]:
    """Per-node liveness: how many of its models are currently demoted (down), and
    until when. Surfaces the gap in 'every node utilised' at a glance."""
    out: Dict[str, dict] = {}
    with _lock:
        nodes = {n: dict(i) for n, i in _nodes.items()}
    now = time.time()
    for node, info in nodes.items():
        down = 0
        total = 0
        worst = 0.0
        for model, full in info.get("models", {}).items():
            p = _perf.get(full)
            if p is None:
                continue
            total += 1
            du = p.get("down_until", 0.0) or 0.0
            if (du and now < du) or (p.get("fail_streak", 0) or 0) >= FAIL_DEMOTE:
                down += 1
                worst = max(worst, du)
        if total:
            out[node] = {
                "models": total, "down": down,
                "down_until": worst, "state": "DOWN" if down else "up",
            }
    return out


def _watchdog_sweep() -> None:
    """One watchdog pass: find nodes whose models have tripped failure demotion and
    try to wake them. A node is considered crashed when ANY of its models is demoted
    (they share one machine, so one dead model == dead node)."""
    for node, h in node_health().items():
        if h["state"] == "DOWN":
            logger.warning("Watchdog: node %s is DOWN (%d/%d models demoted)",
                           node, h["down"], h["models"])
            wake_node(node)


def _probe(full_id: str) -> None:
    """One-shot benchmark to estimate a model's tok/s; throttled."""
    with _lock:
        p = _perf.setdefault(full_id, {"tok_s": 0.0, "latency": 0.0,
                                        "success": 1.0, "n": 0, "last_probe": 0.0,
                                        "fail_streak": 0, "down_until": 0.0})
        if time.time() - p["last_probe"] < PROBE_COOLDOWN:
            return
        p["last_probe"] = time.time()
    try:
        t0 = time.time()
        resp = requests.post(
            ROUTER_CHAT_URL,
            json={"model": full_id,
                  "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                  "max_tokens": PROBE_TOKENS, "stream": False},
            timeout=90,
        )
        elapsed = time.time() - t0
        if resp.status_code == 200:
            d = resp.json()
            nt = (d.get("usage") or {}).get("completion_tokens") or 0
            with _lock:
                pp = _perf.setdefault(full_id, {})
                rate = nt / elapsed if (elapsed > 0 and nt) else 0.0
                if rate:
                    pp["tok_s"] = rate
                pp["latency"] = elapsed
    except Exception:
        pass


def _probe_stale() -> None:
    """Probe a few models whose performance we haven't measured recently."""
    with _lock:
        candidates = [
            full for (_, full, _) in _iter_models()
            if time.time() - _perf.get(full, {}).get("last_probe", 0) >= PROBE_COOLDOWN
            and _perf.get(full, {}).get("tok_s", 0.0) == 0.0
        ]
    for full in candidates[:PROBE_PER_CYCLE]:
        _probe(full)


def _iter_models():
    for bare, lst in _model_map.items():
        for node, full, model in lst:
            yield node, full, model


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def _node_of(full_id: str) -> str:
    if full_id.startswith("lmstudio-"):
        return full_id[len("lmstudio-"):].partition(".")[0]
    return ""


def _score(full_id: str, model: str, task: dict) -> Optional[dict]:
    """Return scoring components for ``full_id`` against ``task``.

    Returns ``None`` when the model is not capable (fails the min_params /
    toolish filter). Otherwise a dict with ``base`` (capability/speed fit),
    ``idle_s`` (seconds since last use), and in-flight counters. The caller
    applies RELATIVE utilisation pressure across the whole candidate pool -- a
    flat boost would be added equally to every idle node and so could never
    reorder them, which is why the pressure is relative (see ``_apply_pressure``).
    """
    params = _params_of(model)
    if params < task.get("min_params", 0.0):
        return None  # capability filter (e.g. reasoning needs >=9B)
    if task.get("toolish") and not _is_toolish(model):
        return None  # tier requires a tool-capable model
    node = _node_of(full_id)
    now = time.time()
    with _lock:
        perf = dict(_perf.get(full_id, {}))
        inflight = _inflight.get(full_id, 0)
        ninflight = _node_inflight.get(node, 0)
        last = _lru.get(full_id, 0.0)
    # Failure-aware: a node in its down window is treated as a last-resort only,
    # so we stop sending it work while it's likely asleep/crashed. It heals
    # automatically once record_outcome sees a success (down_until -> 0).
    down_until = perf.get("down_until", 0.0) or 0.0
    if down_until and now < down_until:
        return {"down": True, "base": -10.0, "idle_s": 0.0,
                "inflight": inflight, "ninflight": ninflight}
    tok_s = perf.get("tok_s") or 0.0
    if not tok_s:
        # Credit never-measured models with a neutral speed so they get tried
        # (exploration) instead of being shunned forever for looking slow.
        tok_s = ASSUMED_TOK_S if perf.get("n", 0) == 0 else 0.0
    speed = min(tok_s / 25.0, 1.0)  # 25 tok/s -> 1.0
    success = perf.get("success")
    success = 1.0 if success is None else success
    fail_streak = perf.get("fail_streak", 0) or 0
    health = 1.0 if fail_streak == 0 else max(0.25, 1.0 - 0.25 * fail_streak)
    lat_tol = task.get("latency_tolerance", 0.5)
    quality_need = task.get("quality_need", 0.5)
    # Prefer a model whose SIZE matches the task's quality need: cheap/summarise
    # work wants a SMALL model (efficiency); hard reasoning wants a BIG/smart one.
    preferred = min(35.0, 0.8 * (50.0 ** quality_need))
    scale = max(preferred * 0.8, 2.0)
    size_fit = math.exp(-(((params - preferred) / scale) ** 2))
    # Latency fit: an interactive task (low tolerance) demands speed; a tolerant
    # background task accepts slow models (so beelink's 9B still earns BG work).
    latency_fit = lat_tol + (1.0 - lat_tol) * speed
    base = (size_fit * latency_fit + 0.15 * success) * health
    # Oversize affinity: a model FAR larger than the task needs (e.g. a 35B brain
    # asked to do a trivial summarise) should NOT be yanked into lightweight work
    # just because it has been idle -- that wastes the fleet's "smart" capacity.
    # Idle pressure is scaled down by how oversized the model is, so small/right-
    # sized nodes still get pulled in (worker bees) while the heavy hitters stay
    # reserved for the hard tasks that actually need them.
    oversize = max(0.0, (params - preferred) / preferred)
    affinity = 1.0 / (1.0 + oversize)
    idle = (now - last) if last else max(now - perf.get("first_seen", now), UTIL_TAU * 4)
    return {"down": False, "base": base, "idle_s": idle, "affinity": affinity,
            "inflight": inflight, "ninflight": ninflight}


def _apply_pressure(scored: list) -> list:
    """Add RELATIVE utilisation pressure and load penalty to a scored pool.

    ``scored`` is a list of ``(comp, *rest, full)`` tuples where ``comp`` is the
    dict from ``_score`` and ``full`` (the final element) is the model id used
    for the LRU tiebreak. Each candidate's idle time is compared to the pool's
    mean: a node quiet longer than its peers is pulled UP, a just-used node is
    pushed DOWN (negative consequence of having been served / under-utilisation
    pressure on the rest). Bounded to ``+/- REL_PRESSURE`` so fit still wins
    among peers but a starved node is actively rotated back in.
    """
    if not scored:
        return []
    idles = [c["idle_s"] for c, *_ in scored]
    avg_idle = sum(idles) / len(idles)
    finals = []
    for comp, *rest in scored:
        full = rest[-1] if rest else comp.get("full_id")
        rel = max(-1.0, min(1.0, (comp["idle_s"] - avg_idle) / UTIL_TAU)) * REL_PRESSURE
        rel *= comp.get("affinity", 1.0)  # don't yank oversized models into light work
        load = INFLOW_PENALTY * comp["inflight"] + 0.5 * INFLOW_PENALTY * comp["ninflight"]
        finals.append((comp["base"] + rel - load, *rest))
    return finals


def _pick(scored: list) -> tuple:
    """Pick the best candidate, load-spreading across near-equal options.

    Among every candidate within ``SPREAD_EPS`` of the top score, the one used
    least recently wins -- so the whole fleet shares work (worker bees) rather
    than one node being hammered while the rest idle.
    """
    best = max(s for s, *_ in scored)
    top = [x for x in scored if x[0] >= best - SPREAD_EPS]
    top.sort(key=lambda x: _lru.get(x[-1], 0.0))
    return top[0]


def _mark_dispatched(full_id: str) -> None:
    """Record that work was just sent to a model/node (for utilisation pressure)."""
    node = _node_of(full_id)
    now = time.time()
    with _lock:
        _lru[full_id] = now
        _node_last_used[node] = now
        _inflight[full_id] = _inflight.get(full_id, 0) + 1
        _node_inflight[node] = _node_inflight.get(node, 0) + 1


def _mark_done(full_id: str) -> None:
    """Record that a dispatched task finished (free up the in-flight slot)."""
    node = _node_of(full_id)
    with _lock:
        _inflight[full_id] = max(0, _inflight.get(full_id, 0) - 1)
        _node_inflight[node] = max(0, _node_inflight.get(node, 0) - 1)


def _mark_selftask_dispatched(node: str) -> None:
    """Count a background self-task as in-flight on ``node`` (per-node cap)."""
    with _lock:
        _node_selftask_inflight[node] = _node_selftask_inflight.get(node, 0) + 1


def _mark_selftask_done(node: str) -> None:
    """Free a node's self-task in-flight slot."""
    with _lock:
        _node_selftask_inflight[node] = max(0, _node_selftask_inflight.get(node, 0) - 1)


def healthy_node_count() -> int:
    """How many nodes are currently UP (not demoted). Used to scale self-task
    concurrency so survivors aren't overloaded when part of the fleet is down."""
    try:
        return sum(1 for h in node_health().values() if h["state"] == "up")
    except Exception:
        return max(1, len(_nodes))


def _node_cap(node: str) -> int:
    """Max concurrent self-tasks a node may run. Explicit overrides win; otherwise
    a size-aware default: a node hosting a big model can absorb more background
    load than a tiny laptop (which stays at the safe default of 1)."""
    if node in NODE_SELFTASK_CAP_MAP:
        return NODE_SELFTASK_CAP_MAP[node]
    biggest = 0
    for model in _nodes.get(node, {}).get("models", {}).values():
        biggest = max(biggest, _params_of(model))
    if biggest >= 20:
        return 3
    if biggest >= 8:
        return 2
    return NODE_SELFTASK_CAP


def select(model: str, task: Optional[dict] = None,
          prefer_nodes: Optional[list] = None) -> Optional[str]:
    """Return a node-prefixed id for ``model``.

    With ``task`` (a dict with min_params / latency_tolerance / quality_need),
    candidates are scored and the best-fit node is chosen. Without ``task``, the
    least-recently-used node is picked (simple load spreading).
    """
    _ensure_refresh_thread()
    discover()
    cands = _model_map.get(model)
    if not cands:
        return None
    if prefer_nodes:
        filt = [c for c in cands if c[0] in prefer_nodes]
        if filt:
            cands = filt
    if not task:
        cands = sorted(cands, key=lambda c: _lru.get(c[1], 0.0))
        node, full, _ = cands[0]
        _mark_dispatched(full)
        return full
    scored = []
    for node, full, m in cands:
        comp = _score(full, m, task)
        if comp is None:
            continue
        scored.append((comp, full))
    if not scored:
        return None
    finals = _apply_pressure(scored)
    sc, full = _pick(finals)
    _mark_dispatched(full)
    _record_choice(full, task, sc)
    return full


def select_any(models, task: Optional[dict] = None,
               prefer_nodes: Optional[list] = None, selftask: bool = False):
    """Return (bare_model, full_id) of the BEST live candidate across the fleet.

    Every candidate of every model in ``models`` is scored against ``task`` and
    the globally best-fit node/model wins -- so a small tool model beats a big
    one for tool-small, and a slow node is still chosen for latency-tolerant
    background work. When ``selftask`` is True, nodes already running their per-
    node cap of background self-tasks are skipped (the overload backstop that
    keeps small laptops from crashing).
    """
    _ensure_refresh_thread()  # also kicks off the metrics server + probing
    discover()
    if task is None:
        for m in models:
            full = select(m, task=None, prefer_nodes=prefer_nodes)
            if full:
                return m, full
        return None, None
    scored = []
    for m in models:
        for node, full, model in _model_map.get(m, []):
            if prefer_nodes and node not in prefer_nodes:
                continue
            if selftask and _node_selftask_inflight.get(node, 0) >= _node_cap(node):
                continue
            comp = _score(full, model, task)
            if comp is None:
                continue
            scored.append((comp, m, full))
    if not scored:
        return None, None
    finals = _apply_pressure(scored)
    sc, m, full = _pick(finals)
    _mark_dispatched(full)
    _record_choice(full, task, sc)
    return m, full


def list_models() -> list:
    discover()
    return sorted(_model_map.keys())


def nodes_for(model: str):
    discover()
    return [(n, f) for (n, f, _) in _model_map.get(model, [])]


def status() -> dict:
    discover()
    out = {}
    for n, v in _nodes.items():
        out[n] = sorted(v["models"].keys())
    return out


def perf_report() -> dict:
    with _lock:
        return {k: dict(v) for k, v in _perf.items()}


def metrics_snapshot() -> dict:
    """Full accumulated view of the fleet: per-model performance + recent choices."""
    now = time.time()
    with _lock:
        models = {}
        for k, v in _perf.items():
            entry = dict(v)
            # Transient utilisation signal so the dashboard can flag idle/overloaded
            # nodes -- the negative consequence for under-utilisation made visible.
            entry["inflight"] = _inflight.get(k, 0)
            entry["node_inflight"] = _node_inflight.get(_node_of(k), 0)
            entry["idle_s"] = round(now - _lru.get(k, v.get("first_seen", now)), 1)
            models[k] = entry
        return {
            "generated_at": now,
            "models": models,
            "recent_decisions": list(_decisions[-_DECISION_CAP:]),
            "fleet": status(),
        }


def log_summary() -> None:
    rows = []
    now = time.time()
    with _lock:
        for full, p in _perf.items():
            node = _node_of(full)
            idle = now - _lru.get(full, p.get("first_seen", now))
            rows.append((p.get("tok_s", 0.0), p.get("success", 1.0), p.get("n", 0),
                         p.get("fail_streak", 0), p.get("down_until", 0.0),
                         _inflight.get(full, 0), _node_inflight.get(node, 0), idle, full))
    rows.sort(reverse=True)
    logger.info("FLEET METRICS (%d models tracked):", len(rows))
    for tok_s, succ, n, fs, down, inf, ninf, idle, full in rows[:16]:
        flag = ""
        if down and time.time() < down:
            flag = "  [DOWN]"
        logger.info("  %-50s tok/s=%.1f succ=%.2f calls=%d fs=%d in=%d nin=%d idle=%.0fs%s",
                    full, tok_s, succ, n, fs, inf, ninf, idle, flag)
    # Node-level liveness so a crashed machine is visible at a glance (the gap in
    # "every node utilised"), not just buried in per-model rows.
    down_nodes = [n for n, h in node_health().items() if h["state"] == "DOWN"]
    if down_nodes:
        logger.warning("FLEET NODES DOWN: %s (watchdog will attempt wake)", ", ".join(sorted(down_nodes)))
    else:
        logger.info("All %d nodes UP", len(node_health()))


def _start_metrics_server() -> None:
    """Expose /metrics (JSON) inside the container so it can be curled."""
    global _metrics_started
    if _metrics_started:
        return
    port = int(os.getenv("FLEET_METRICS_PORT", "9099"))
    try:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class _H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/metrics"):
                    body = json.dumps(metrics_snapshot()).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("0.0.0.0", port), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    except Exception as e:
        logger.warning("fleet metrics server failed: %s", e)
        return
    _metrics_started = True
    logger.info("fleet metrics server on 0.0.0.0:%d/metrics", port)
