"""Dynamic, self-tuning fleet registry + routing.

DEPRECATED: fleet/router ownership moves to auto-router. This module is kept
functionally intact for now but should be considered read-only; new routing
logic belongs in auto-router. See docs/LLD_UNIFIED_FLEET.md W-21.

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

import csv
import json
import logging
import math
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
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

# Load-spreading hardening. The earlier design only ADDED relative pressure on top
# of `base`, so a model with a consistently higher base won every time and the
# fleet piled onto it while peers idled. Worse, never-used models were capped at an
# `idle_s` of ~480s, making them look LESS idle than long-idle-used models, so they
# were deprioritised -- backwards. The fix: a fit GATE keeps grossly-unfit models
# out while letting a wide peer set compete, then IDLE + NODE-IDLE pressure
# DOMINATE base (which becomes a light tiebreak). "Never used" is treated as the
# most idle, so fresh/idle models are actively rotated in. Node idleness drags work
# toward machines that have been quiet, spreading load across the whole fleet.
GATE_FRAC = float(os.getenv("FLEET_GATE_FRAC", "0.5"))        # keep candidates >= GATE_FRAC*best_base
NODE_PRESSURE = float(os.getenv("FLEET_NODE_PRESSURE", "0.6")) # pull work toward idle nodes
BASE_WEIGHT = float(os.getenv("FLEET_BASE_WEIGHT", "0.15"))    # base is a light tiebreak
IDLE_NEVER = float(os.getenv("FLEET_IDLE_NEVER", "1000000.0")) # "never used" == most idle

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

# ---------------------------------------------------------------------------
# Value layer: route by BENCHMARKED VALUE, not just latency + reliability.
#
# The scoring above uses measured speed (tok/s) and observed success
# (reliability). That is necessary but not sufficient: a model can be up and fast
# yet produce weak output, and a large model on weak hardware is slow/bad. The
# `lms` toolkit (git/lms) benchmarks models on each machine with deterministic
# evaluators and emits a capability matrix + model-fit report. We ingest those
# artifacts here and fold a *value* multiplier into the base score, so routing
# prefers models that actually earn their keep for the task at hand and avoids
# models that benchmark poorly on their hardware. With no benchmark data present
# the multiplier is 1.0, so behaviour is identical to before (degrades safe).
LMS_RUNS_DIR = os.getenv("FLEET_LMS_RUNS_DIR", "/home/scott/git/lms/runs")
LMS_RELOAD_INTERVAL = float(os.getenv("FLEET_LMS_RELOAD_INTERVAL", "120"))

_value_lock = threading.Lock()
_value_index: Dict[Tuple[str, str, str], dict] = {}   # (node, model, family) -> cap row
_fit_index: Dict[Tuple[str, str], str] = {}            # (node, model) -> fit_grade
_summary_index: Dict[Tuple[str, str], dict] = {}       # (node, model) -> summary row
_value_loaded_at = 0.0


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
# Value layer (benchmarked value, from the `lms` toolkit)
# ---------------------------------------------------------------------------
def _norm_node(name: str) -> str:
    """Normalise a node/host name for matching benchmark data.

    Fleet nodes are ``lmstudio-<node>``; lms host names may be ``destroyer`` or a
    Tailscale FQDN like ``destroyer.tailcb8954.ts.net``. Collapse both to the short
    lower-cased host label."""
    n = (name or "").lower()
    if n.startswith("lmstudio-"):
        n = n[len("lmstudio-"):]
    return n.split(".")[0]


def _norm_model(model: str) -> str:
    """Normalise a model id: drop any ``lmstudio-<node>.`` prefix, keep the bare id."""
    m = (model or "").lower()
    if m.startswith("lmstudio-"):
        m = m.split(".", 1)[1] if "." in m else m
    return m


def _task_family(task: Optional[dict]) -> str:
    """Map a swarm task to an lms benchmark task family.

    Callers may set ``task['task_family']`` explicitly for accuracy. Otherwise we
    infer from the task's declared need: hard/large reasoning -> agent_planning,
    quality-hungry -> coding, fast interactive or latency-tolerant background work
    -> structured_output (summarise/extract)."""
    if not task:
        return "structured_output"
    tf = task.get("task_family")
    if tf:
        return tf
    minp = float(task.get("min_params", 0) or 0)
    qn = float(task.get("quality_need", 0.5) or 0.5)
    lat = float(task.get("latency_tolerance", 0.5) or 0.5)
    if minp >= 9:
        return "agent_planning"
    if qn >= 0.7:
        return "coding"
    if lat < 0.3:
        return "structured_output"
    return "structured_output"


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_GRADE_MULT = {"a": 1.25, "b": 1.0, "c": 0.8, "d": 0.5, "f": 0.15}
_FIT_MULT = {"good": 1.0, "borderline": 0.9, "risky": 0.6, "poor": 0.3, "unknown": 1.0}


def _load_value_data(force: bool = False) -> None:
    """Ingest lms benchmark artifacts (capability_matrix.csv, run_summary.csv,
    model_fit.csv) from ``LMS_RUNS_DIR`` into the in-memory value indexes.

    Cheap + self-throttled: only re-reads every ``LMS_RELOAD_INTERVAL`` seconds.
    A missing/empty dir is fine -- the value multiplier stays neutral (1.0) and the
    fleet behaves exactly as before."""
    global _value_index, _fit_index, _summary_index, _value_loaded_at
    now = time.time()
    if not force and now - _value_loaded_at < LMS_RELOAD_INTERVAL:
        return
    with _value_lock:
        _value_loaded_at = now
        vi: Dict[Tuple[str, str, str], dict] = {}
        fi: Dict[Tuple[str, str], str] = {}
        si: Dict[Tuple[str, str], dict] = {}
        base = Path(LMS_RUNS_DIR)
        if not base.exists():
            _value_index, _fit_index, _summary_index = vi, fi, si
            return
        for run in sorted(p for p in base.iterdir() if p.is_dir()):
            cap = run / "capability_matrix.csv"
            if cap.exists():
                try:
                    with cap.open(newline="", encoding="utf-8") as fh:
                        for row in csv.DictReader(fh):
                            host = _norm_node(row.get("host_name") or row.get("host") or "")
                            mk = _norm_model(row.get("model_key") or row.get("model_id") or "")
                            fam = (row.get("task_family") or "").strip().lower()
                            if not host or not mk or not fam:
                                continue
                            vi[(host, mk, fam)] = {
                                "grade": (row.get("grade") or "").strip().lower(),
                                "route_score": _to_float(row.get("score")),
                                "recommended_use": (row.get("recommended_use") or ""),
                                "avoid_use": (row.get("avoid_use") or ""),
                            }
                except Exception as exc:
                    logger.warning("value-layer: skip %s: %s", cap, exc)
            summ = run / "run_summary.csv"
            if summ.exists():
                try:
                    with summ.open(newline="", encoding="utf-8") as fh:
                        for row in csv.DictReader(fh):
                            host = _norm_node(row.get("host_name") or row.get("host") or "")
                            mk = _norm_model(row.get("model_key") or row.get("model_id") or "")
                            if not host or not mk:
                                continue
                            si[(host, mk)] = {
                                "ok_rate": _to_float(row.get("ok_rate")),
                                "eval_ok_rate": _to_float(row.get("eval_ok_rate")),
                                "eval_score_avg": _to_float(row.get("eval_score_avg")),
                                "tps_med": _to_float(row.get("tps_med")),
                            }
                except Exception as exc:
                    logger.warning("value-layer: skip %s: %s", summ, exc)
            fit = run / "model_fit.csv"
            if fit.exists():
                try:
                    with fit.open(newline="", encoding="utf-8") as fh:
                        for row in csv.DictReader(fh):
                            mk = _norm_model(row.get("model_key") or row.get("model_id") or "")
                            if not mk:
                                continue
                            host = _norm_node(row.get("host_name") or run.name or "")
                            fi[(host, mk)] = (row.get("fit_grade") or "unknown").strip().lower()
                except Exception as exc:
                    logger.warning("value-layer: skip %s: %s", fit, exc)
        _value_index, _fit_index, _summary_index = vi, fi, si
        logger.info(
            "value-layer: loaded %d capability rows, %d fit rows, %d summary rows from %s",
            len(vi), len(fi), len(si), base,
        )


def _value_factor(full_id: str, model: str, task_family: str) -> float:
    """Return a value multiplier (clamped ~[0.05, 1.3]) for ``(node, model, family)``.

    1.0 when no benchmark data exists (degrades safe). Below 1.0 penalises models
    that benchmark poorly for this task / on this hardware; above 1.0 rewards ones
    that earn their keep. A model flagged ``avoid_use`` for the task (or grade F)
    is crushed so it is only used as a last resort."""
    node = _norm_node(_node_of(full_id))
    mk = _norm_model(model)
    fit = _fit_index.get((node, mk), "unknown")
    fit_mult = _FIT_MULT.get(fit, 1.0)
    cap = _value_index.get((node, mk, task_family))
    if cap:
        grade = cap.get("grade") or ""
        avoid = (cap.get("avoid_use") or "").strip().lower()
        # A benchmark run that could not execute (ok_rate == 0 -> route_score 0/None)
        # is NOT evidence the model is weak -- e.g. the node's LM Studio 500'd or was
        # mid-load-swap during benchmarking. Treating that as grade-F would wrongly
        # crush a perfectly good model, so fall through to neutral (fit_mult only).
        rs = cap.get("route_score")
        if rs is None or float(rs or 0) <= 0:
            return fit_mult
        if grade == "f" or "avoid" in avoid or task_family in avoid:
            return max(0.05, 0.15 * fit_mult)
        if rs is not None:
            # route_score in [0,1]: map 0.5->1.0, 1.0->1.25, 0.0->0.5
            rs_mult = 0.5 + max(0.0, min(1.0, rs)) * 0.75
            return max(0.05, min(1.3, fit_mult * rs_mult))
        return max(0.05, min(1.3, fit_mult * _GRADE_MULT.get(grade, 1.0)))
    return fit_mult


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover(force: bool = False) -> None:
    """Refresh node/model maps from the router. Cached via TTL; the background
    thread keeps it warm so callers rarely block."""
    global _nodes, _model_map, _last_refresh
    # Ingest lms benchmark value data (self-throttled; cheap when fresh). Runs on
    # every discover so the value layer tracks new benchmark runs without a restart.
    _load_value_data()
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
    # Value layer: fold benchmarked value into the base score. `_value_factor`
    # returns 1.0 when no lms benchmark data exists, so this is a strict superset
    # of the old behaviour. It penalises models that benchmark poorly for this
    # task / on this hardware (e.g. a large model on a weak box, or a model that
    # produces weak output) and rewards ones that earn their keep.
    value = _value_factor(full_id, model, _task_family(task))
    base = base * value
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
            "value": value, "inflight": inflight, "ninflight": ninflight}


def _apply_pressure(scored: list) -> list:
    """Spread load across the fleet like worker bees instead of hammering one model.

    Among candidates that pass a fit GATE (so grossly-unfit models are excluded but
    a wide peer set competes), IDLE + NODE-IDLE pressure DOMINATE the final score
    while ``base`` (capability x measured speed x benchmarked value) is only a light
    tiebreak. ``idle_s`` is the real time-since-last-use; a never-used model/node is
    treated as the MOST idle so fresh/idle capacity is actively rotated in. The
    negative consequence of under-utilisation (a quiet model) is a positive pull."""
    if not scored:
        return []
    now = time.time()
    best_base = max((c["base"] for c, *_ in scored), default=0.0)
    gate = max(best_base * GATE_FRAC, 0.05)
    pool = [(c, *rest) for c, *rest in scored if c["base"] >= gate] or scored

    def _idle(ts: float) -> float:
        return (now - ts) if ts else IDLE_NEVER

    model_idles = []
    for c, *rest in pool:
        full = rest[-1] if rest else c.get("full_id")
        model_idles.append(_idle(_lru.get(full, 0.0)))
    avg_idle = sum(model_idles) / len(model_idles)

    node_idles = []
    for c, *rest in pool:
        full = rest[-1] if rest else c.get("full_id")
        node_idles.append(_idle(_node_last_used.get(_node_of(full), 0.0)))
    avg_node_idle = sum(node_idles) / len(node_idles)

    finals = []
    for (comp, *rest), midle, nidle in zip(pool, model_idles, node_idles):
        full = rest[-1] if rest else comp.get("full_id")
        rel = max(-1.0, min(1.0, (midle - avg_idle) / UTIL_TAU)) * REL_PRESSURE
        rel *= comp.get("affinity", 1.0)  # don't yank oversized models into light work
        nrel = max(-1.0, min(1.0, (nidle - avg_node_idle) / UTIL_TAU)) * NODE_PRESSURE
        load = INFLOW_PENALTY * comp["inflight"] + 0.5 * INFLOW_PENALTY * comp["ninflight"]
        finals.append((BASE_WEIGHT * comp["base"] + rel + nrel - load, *rest))
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
