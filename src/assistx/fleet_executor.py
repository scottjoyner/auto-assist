"""Central fleet executor — the "helper" that does the work so agents don't have to.

Agents only need an LM Studio endpoint. The executor:
1. Discovers nodes + their model inventory from the router, probing each for
   live LM Studio endpoints and the models they have loaded.
2. Polls AssistX for READY tasks.
3. For each task, finds the **best** capable node:
   - ``llm`` tasks with a specific model → node that has it loaded.
   - Generic ``llm`` → round-robin across all LM Studio nodes.
   - ``script`` → subprocess on the executor host.
4. Handles the full lifecycle: claim → execute → complete (idempotent).
5. Tracks node health: skips unresponsive nodes, logs failures.

No agent-side code beyond running LM Studio.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Singleton reference to the running fleet executor for API access
_fleet_executor_instance: Optional[FleetExecutor] = None

EXECUTOR_INTERVAL = float(os.getenv("FLEET_EXECUTOR_INTERVAL", "2"))
LMSTUDIO_PORT = int(os.getenv("FLEET_LMSTUDIO_PORT", "1234"))
MAX_CONCURRENT_LLM = int(os.getenv("FLEET_EXECUTOR_LLM_CONCURRENCY", "64"))
MAX_CONCURRENT_SCRIPT = int(os.getenv("FLEET_EXECUTOR_SCRIPT_CONCURRENCY", "16"))
NODE_HEALTH_TTL = float(os.getenv("FLEET_NODE_HEALTH_TTL", "90"))
# Composite valuation: quality_weight * eval_score + (1-quality_weight) * normalized_tps
# 0.0 = pure speed (TPS), 1.0 = pure quality (eval_score)
ROUTING_QUALITY_WEIGHT = float(os.getenv("FLEET_ROUTING_QUALITY_WEIGHT", "0.5"))
# Per-node concurrency weight: "host:weight,host2:weight2" — lets a node
# that can run N concurrent LM Studio sessions pull N× the work.
NODE_CONCURRENCY = {}
_raw = os.getenv("FLEET_NODE_CONCURRENCY", "")
for _pair in _raw.split(","):
    _pair = _pair.strip()
    if ":" in _pair:
        _h, _w = _pair.rsplit(":", 1)
        try:
            NODE_CONCURRENCY[_h.strip()] = max(1, int(_w))
        except ValueError:
            pass
BASIC_AUTH_USER = os.getenv("FLEET_BASIC_AUTH_USER", "admin")
BASIC_AUTH_PASS = os.getenv("FLEET_BASIC_AUTH_PASS", "gluhlaf8")
ASSISTX_URL = os.getenv("FLEET_ASSISTX_URL", "http://assistx:8000")
ROUTER_URL = os.getenv("FLEET_ROUTER_URL", "http://router:8088")
KNOWN_HOSTS = os.getenv("FLEET_KNOWN_HOSTS", "").split(",") if os.getenv("FLEET_KNOWN_HOSTS") else []

# Benchmark-based routing data paths
FLEET_LOADOUT_PATH = os.getenv("FLEET_LOADOUT_PATH", "/home/scott/git/lms/fleet_loadout.json")
FLEET_STATE_PATH = os.getenv("FLEET_STATE_PATH", "/home/scott/git/lms/fleet_state.json")


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _http(
    method: str,
    url: str,
    data: Optional[dict] = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    headers = {}
    if BASIC_AUTH_USER and BASIC_AUTH_PASS:
        import base64
        raw = f"{BASIC_AUTH_USER}:{BASIC_AUTH_PASS}"
        headers["Authorization"] = f"Basic {base64.b64encode(raw.encode()).decode()}"
    body = json.dumps(data).encode() if data else None
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
        except Exception:
            detail = {"error": str(e)}
        return e.code, detail
    except Exception as e:
        return 0, {"error": str(e)}


class FleetRouting:
    """Loads and provides benchmark-based routing intelligence from lms repo."""

    def __init__(self) -> None:
        self._loadout: dict = {}
        self._state: dict = {}
        self._routing: dict = {}
        self._model_to_node: dict = {}
        self._node_models: dict = {}
        self._model_perf: dict = {}
        self._node_specs: dict = {}  # hostname -> {ram_gib, vram_gib}
        self._model_sizes: dict = {}  # model -> estimated_size_gib
        self._load()

    def _estimate_model_size(self, model: str) -> float:
        """Estimate model size in GiB from model name."""
        ml = model.lower()
        # Extract parameter count from name (e.g., "35b", "7b", "1.2b", "4b", "0.8b")
        import re
        # Match patterns like 35b, 7b, 1.2b, 4b, 0.8b, 27b, 70b, 72b
        match = re.search(r'(\d+(?:\.\d+)?)b', ml)
        if match:
            params_b = float(match.group(1))
            # Rough estimate: 4-bit quant = params_b * 0.5 GiB, 8-bit = params_b GiB
            # Most local models are 4-bit quantized
            return params_b * 0.5
        # Fallback for known model patterns without 'b' suffix
        if 'gemma-4-12b' in ml or 'qwen3.5-14b' in ml:
            return 7.0
        if 'gemma-4-31b' in ml or 'qwen3.5-27b' in ml or 'qwen3.5-35b' in ml or 'ornith-1.0-35b' in ml:
            return 18.0
        if 'qwen3.6-35b' in ml:
            return 18.0
        if 'gpt-oss-20b' in ml:
            return 10.0
        if 'granite-4-h-tiny' in ml or 'granite-4-h' in ml:
            return 0.5
        # Default small model
        return 2.0

    def _load(self) -> None:
        # Load fleet_loadout.json (desired state)
        try:
            with open(FLEET_LOADOUT_PATH, "r") as f:
                self._loadout = json.load(f)
        except Exception as e:
            logger.warning("fleet executor: failed to load loadout: %s", e)
        # Load fleet_state.json (real-time state with perf metrics)
        try:
            with open(FLEET_STATE_PATH, "r") as f:
                self._state = json.load(f)
        except Exception as e:
            logger.warning("fleet executor: failed to load state: %s", e)

        # Build routing tables
        self._build_routing()

    def _build_routing(self) -> None:
        """Build model -> best node routing from loadout + state with composite valuation."""
        # Build model -> best node mapping using composite valuation (TPS * quality)
        # First pass: collect all model performance data from state
        model_candidates: dict[str, list[dict]] = {}  # model -> list of {node, tps, eval, concurrency, score}
        
        if "nodes" in self._state:
            for node in self._state["nodes"]:
                hostname = node.get("name") or node.get("hostname")
                if not hostname:
                    continue
                if not node.get("live", False):
                    continue
                # Store node hardware specs for model fitting
                self._node_specs[hostname] = {
                    "ram_gib": node.get("hardware", {}).get("ram_gib"),
                    "vram_gib": node.get("hardware", {}).get("vram_gib"),
                    "cpu": node.get("hardware", {}).get("cpu"),
                }
                for model_info in node.get("models", []):
                    if isinstance(model_info, dict):
                        mkey = model_info.get("model_key") or model_info.get("model")
                        if not mkey:
                            continue
                        tps = model_info.get("tps_med", 0) or 0
                        eval_score = model_info.get("eval_score", 0) or 0
                        if tps <= 0:
                            continue  # not available/benchmarked
                        concurrency = model_info.get("concurrency", {})
                        tier = concurrency.get("tier", 1) if isinstance(concurrency, dict) else 1
                        # Composite valuation: TPS * (eval_score + epsilon) rewards both speed and quality
                        eps = 0.1
                        composite_score = tps * (eval_score + eps)
                        
                        model_candidates.setdefault(mkey, []).append({
                            "node": hostname,
                            "tps": tps,
                            "eval_score": eval_score,
                            "concurrency_tier": tier,
                            "composite_score": composite_score,
                        })
        
        # Second pass: select best node per model by composite score
        for model, candidates in model_candidates.items():
            candidates.sort(key=lambda c: c["composite_score"], reverse=True)
            best = candidates[0]
            available_on = [c["node"] for c in candidates]
            self._model_to_node[model] = best["node"]
            self._routing[model] = {
                "best_node": best["node"],
                "available_on": available_on,
                "max_concurrency": best["concurrency_tier"],
                "best_tps": best["tps"],
                "best_eval": best["eval_score"],
                "best_composite": best["composite_score"],
            }

        # Also enrich loadout routing with quality data where available
        if "routing" in self._loadout:
            for model, info in self._loadout["routing"].items():
                if isinstance(info, dict) and info.get("best_node"):
                    # Already have better data from state; skip unless model not in state
                    if model not in self._model_to_node:
                        self._model_to_node[model] = info["best_node"]
                        self._routing[model] = {
                            "best_node": info["best_node"],
                            "available_on": info.get("available_on", []),
                            "max_concurrency": info.get("max_concurrency", 1),
                            "best_tps": info.get("best_tps", 0),
                        }

        # Enrich with state data (real-time metrics) for per-node model perf
        if "nodes" in self._state:
            for node in self._state["nodes"]:
                hostname = node.get("name") or node.get("hostname")
                if not hostname:
                    continue
                self._node_models[hostname] = []
                for model_info in node.get("models", []):
                    if isinstance(model_info, dict):
                        mkey = model_info.get("model_key") or model_info.get("model")
                        if mkey:
                            self._node_models[hostname].append(mkey)
                            # Store performance metrics
                            tps = model_info.get("tps_med", 0) or 0
                            eval_score = model_info.get("eval_score", 0) or 0
                            eps = 0.1
                            self._model_perf[f"{hostname}:{mkey}"] = {
                                "tps_med": tps,
                                "ttft_med": model_info.get("ttft_med", 0),
                                "eval_score": eval_score,
                                "concurrency_tier": model_info.get("concurrency_tier", 1),
                                "load_s": model_info.get("load_s", 0),
                                "ok": model_info.get("ok", True),
                                "composite_score": tps * (eval_score + eps),
                            }

    def get_best_node_for_model(self, model: str) -> Optional[str]:
        """Get the best node for a specific model."""
        return self._model_to_node.get(model)

    def get_fallback_nodes(self, model: str) -> list[str]:
        """Get fallback nodes for a model in priority order."""
        info = self._routing.get(model, {})
        return info.get("available_on", [])

    def get_model_concurrency(self, model: str) -> int:
        """Get max concurrency for a model."""
        info = self._routing.get(model, {})
        return info.get("max_concurrency", 1)

    def get_node_models(self, hostname: str) -> list[str]:
        """Get models available on a node."""
        return self._node_models.get(hostname.lower(), [])

    def get_model_perf(self, hostname: str, model: str) -> dict:
        """Get performance metrics for a model on a node."""
        return self._model_perf.get(f"{hostname.lower()}:{model}", {})

    def check_model_fit(self, hostname: str, model: str) -> dict:
        """Check if a model fits on a node based on hardware specs.
        Returns dict with 'fits': bool, 'reason': str, 'model_size_gib': float, 'available_ram_gib': float, 'vram_gib': float."""
        specs = self._node_specs.get(hostname, {})
        if not specs:
            return {"fits": True, "reason": "no_hardware_data", "model_size_gib": 0, "available_ram_gib": 0, "vram_gib": 0}
        
        ram_gib = specs.get("ram_gib")
        vram_gib = specs.get("vram_gib")
        available_ram = ram_gib  # Use total RAM as budget (conservative)
        
        # Estimate model size
        model_size = self._estimate_model_size(model)
        self._model_sizes[model] = model_size
        
        # For CPU-only nodes (no VRAM), model must fit in system RAM
        # For nodes with VRAM, model can use VRAM + some system RAM
        if vram_gib and vram_gib > 0:
            # Can use VRAM + some RAM for offloading
            # Require model to fit in VRAM * 1.5 (allows some offloading)
            effective_vram = vram_gib * 1.5
            if model_size <= effective_vram:
                return {"fits": True, "reason": "fits_in_vram", "model_size_gib": model_size, "available_ram_gib": available_ram, "vram_gib": vram_gib}
            # Check if it fits in RAM with offloading
            if model_size <= available_ram * 0.9:  # Leave 10% headroom
                return {"fits": True, "reason": "fits_in_ram_with_offload", "model_size_gib": model_size, "available_ram_gib": available_ram, "vram_gib": vram_gib}
            return {"fits": False, "reason": f"model_too_large_for_{vram_gib}GiB_VRAM_and_{available_ram}GiB_RAM", "model_size_gib": model_size, "available_ram_gib": available_ram, "vram_gib": vram_gib}
        else:
            # CPU-only node, model must fit in system RAM
            if model_size <= available_ram * 0.8:  # Leave 20% headroom for OS/other
                return {"fits": True, "reason": "fits_in_ram", "model_size_gib": model_size, "available_ram_gib": available_ram, "vram_gib": 0}
            return {"fits": False, "reason": f"model_too_large_for_{available_ram}GiB_RAM", "model_size_gib": model_size, "available_ram_gib": available_ram, "vram_gib": 0}

    def reload(self) -> None:
        """Reload routing data from disk."""
        self._load()


# Global routing instance
_ROUTING: Optional[FleetRouting] = None


def _get_routing() -> FleetRouting:
    global _ROUTING
    if _ROUTING is None:
        _ROUTING = FleetRouting()
    return _ROUTING


class FleetExecutor:
    """Central task executor — claims READY tasks and runs them against fleet
    nodes by capability. Runs as a daemon thread inside the assistx process."""

    def __init__(self) -> None:
        self._nodes: list[dict] = []
        self._node_lock = threading.Lock()
        self._llm_sem = threading.Semaphore(MAX_CONCURRENT_LLM)
        self._script_sem = threading.Semaphore(MAX_CONCURRENT_SCRIPT)
        self._rr_index: int = 0
        self._pick_count: dict[str, int] = {}
        self._node_inflight: dict[str, int] = {}
        self._node_semaphores: dict[str, threading.Semaphore] = {}
        self._node_latency: dict[str, float] = {}
        self._tld = os.getenv("TAILSCALE_DOMAIN", "tailcb8954.ts.net")
        # Load model routing data from LMS benchmark results
        self._model_routing: dict = {}
        self._model_best_node: dict = {}
        self._model_fallbacks: dict = {}
        self._model_tps: dict = {}
        self._model_max_concurrency: dict = {}
        self._load_routing_data()

    def _load_routing_data(self) -> None:
        """Load model routing data with composite valuation (TPS * quality) from fleet_state.json."""
        import json
        import os
        # Try multiple locations for fleet_state.json (has both TPS and eval_score)
        state_paths = [
            "/home/scott/git/lms/fleet_state.json",
            "/app/fleet_state.json",
            os.path.join(os.path.dirname(__file__), "..", "..", "fleet_state.json"),
        ]
        for path in state_paths:
            try:
                with open(path, "r") as f:
                    state = json.load(f)
                if "nodes" not in state:
                    continue
                
                # Build composite scores per model per node
                model_candidates: dict[str, list[dict]] = {}
                for node in state["nodes"]:
                    hostname = node.get("name") or node.get("hostname")
                    if not hostname or not node.get("live", False):
                        continue
                    for model_info in node.get("models", []):
                        if isinstance(model_info, dict):
                            mkey = model_info.get("model_key") or model_info.get("model")
                            if not mkey:
                                continue
                            tps = model_info.get("tps_med", 0) or 0
                            eval_score = model_info.get("eval_score", 0) or 0
                            if tps <= 0:
                                continue
                            concurrency = model_info.get("concurrency", {})
                            tier = concurrency.get("tier", 1) if isinstance(concurrency, dict) else 1
                            # Composite valuation: TPS * (eval_score + epsilon)
                            eps = 0.1
                            composite = tps * (eval_score + eps)
                            model_candidates.setdefault(mkey.lower(), []).append({
                                "node": hostname.lower(),
                                "tps": tps,
                                "eval_score": eval_score,
                                "composite": composite,
                                "concurrency_tier": tier,
                            })
                
                # Select best node per model by composite score
                for model, candidates in model_candidates.items():
                    candidates.sort(key=lambda c: c["composite"], reverse=True)
                    best = candidates[0]
                    self._model_best_node[model] = best["node"]
                    self._model_fallbacks[model] = [c["node"] for c in candidates[1:]]
                    self._model_tps[model] = best["tps"]
                    self._model_max_concurrency[model] = best["concurrency_tier"]
                    self._model_routing[model] = {
                        "best_node": best["node"],
                        "best_tps": best["tps"],
                        "best_eval": best["eval_score"],
                        "best_composite": best["composite"],
                        "fallbacks": self._model_fallbacks[model],
                    }
                
                logger.info("fleet executor: loaded composite routing for %d models from %s", len(self._model_routing), path)
                return
            except Exception:
                continue
        logger.warning("fleet executor: could not load composite routing from fleet_state.json")

    @staticmethod
    def _resolve(hostname: str, tld: str) -> Optional[str]:
        """Resolve a hostname's tailscale IP via DNS. Retries a few times so
        transient blips don't drop a node from the fleet."""
        import socket
        fqdn = f"{hostname}.{tld}" if "." not in hostname else hostname
        last_err: Exception | None = None
        for _attempt in range(3):
            try:
                return socket.getaddrinfo(fqdn, 1234)[0][4][0]
            except Exception as e:  # transient DNS/network blip
                last_err = e
                time.sleep(0.3)
        return None

    def _refresh_nodes(self) -> None:
        now = time.time()

        # Build seed nodes from router + known hosts config.
        seen_hostnames = set()
        seed_nodes: list[dict] = []

        st, body = _http("GET", f"{ROUTER_URL}/api/fleet/nodes", timeout=10)
        if st == 200:
            nodes = body.get("nodes") if isinstance(body, dict) else body
            if isinstance(nodes, list):
                for n in nodes:
                    hostname = n.get("hostname") or n.get("ip", "")
                    if not hostname:
                        continue
                    # Prefer the router-provided IP (already resolved via
                    # Tailscale on the router side).  Inside this container we
                    # may not have Tailscale magic DNS, so resolving hostnames
                    # here would silently drop nodes.  Keep the IP as a hint.
                    ip = n.get("ip") or ""
                    seed_nodes.append({"hostname": hostname, "ip": ip})
                    seen_hostnames.add(hostname)

        # Inject any known hosts not already tracked by the router.
        for h in KNOWN_HOSTS:
            h = h.strip()
            if h and h not in seen_hostnames:
                seed_nodes.append({"hostname": h})
                seen_hostnames.add(h)

        if not seed_nodes:
            logger.warning("fleet executor: no seed nodes from router or config")
            return

        enriched = []
        # Keep previously-seen nodes around (with their old IP/caps) so a
        # transient blip doesn't drop a machine from the fleet — we just
        # re-probe it next cycle instead of forgetting it.
        prev_by_host = {n.get("hostname"): n for n in self._nodes}
        for n in seed_nodes:
            hostname = n.get("hostname", "")
            if not hostname:
                continue
            # Prefer resolving the hostname via Tailscale DNS (authoritative
            # 100.x.x.x addresses).  The router may advertise a docker-internal
            # IP (e.g. 172.26.0.5) for nodes on its own subnet, which is NOT
            # reachable from this container — so only fall back to the router's
            # reported IP when DNS resolution fails (e.g. macbook-air not in
            # Tailscale DNS).
            ip = self._resolve(hostname, self._tld)
            if not ip:
                hint = (n.get("ip") or "").strip()
                if hint and not hint.startswith("172."):
                    ip = hint
            if not ip:
                prev = prev_by_host.get(hostname)
                if prev:
                    logger.warning(
                        "fleet executor: %s temporarily unresolvable, keeping (will retry)",
                        hostname,
                    )
                    prev["last_seen"] = now
                    prev["lmstudio_ok"] = False
                    enriched.append(prev)
                else:
                    logger.warning("fleet executor: cannot resolve %s, skipping", hostname)
                continue

            caps = set(n.get("capabilities") or [])
            caps.add("linux")
            models = self._probe_models(ip)
            known = hostname in set(h.strip() for h in KNOWN_HOSTS)
            if models is not None:
                caps.add("llm")
                n["loaded_models"] = models
                n["lmstudio_ok"] = True
            else:
                # Probe failed this cycle. If we previously knew this node had
                # LM Studio, or it's an explicitly-configured known host, keep
                # the llm capability so a transient blip (model load, brief
                # overload) doesn't drop it from the fleet. We re-probe next
                # cycle. Only hard-drop if we've never seen it serve models.
                prev = prev_by_host.get(hostname)
                had_llm = prev and prev.get("lmstudio_ok")
                if had_llm or known:
                    caps.add("llm")
                    n["loaded_models"] = (prev or {}).get("loaded_models", [])
                    n["lmstudio_ok"] = bool(had_llm)
                    if had_llm:
                        logger.warning(
                            "fleet executor: %s LM Studio probe flaky, retaining capability",
                            hostname,
                        )
                else:
                    n["loaded_models"] = []
                    n["lmstudio_ok"] = False
            if self._probe_script():
                caps.add("script")
            n["capabilities"] = list(caps)
            n["ip"] = ip
            n["hostname"] = hostname
            n["weight"] = NODE_CONCURRENCY.get(hostname, 1)
            n["last_seen"] = now
            enriched.append(n)

            # Create per-node semaphore to limit concurrent LM Studio requests
            # to the node's weight (concurrency capacity).
            if n.get("lmstudio_ok"):
                weight = max(1, n.get("weight", 1))
                with self._node_lock:
                    if hostname not in self._node_semaphores:
                        self._node_semaphores[hostname] = threading.Semaphore(weight)

        with self._node_lock:
            self._nodes = enriched

        for n in enriched:
            _http(
                "POST",
                f"{ROUTER_URL}/api/fleet/node-report",
                data={
                    "hostname": n["hostname"],
                    "capabilities": n.get("capabilities", []),
                    "loaded": n.get("loaded_models", []),
                    "library": n.get("loaded_models", []),
                    "health": {"lmstudio": n.get("lmstudio_ok", False)},
                },
                timeout=10,
            )
        llm_count = sum(1 for n in enriched if n.get("lmstudio_ok"))
        total_models = sum(len(n.get("loaded_models", [])) for n in enriched if n.get("lmstudio_ok"))
        if llm_count:
            logger.info(
                "fleet executor: %d nodes with LM Studio (%d models): %s",
                llm_count, total_models,
                {n["hostname"]: len(n.get("loaded_models", [])) for n in enriched if n.get("lmstudio_ok")},
            )
        else:
            logger.info("fleet executor: no LM Studio nodes found")

    @staticmethod
    def _probe_models(ip: str) -> Optional[list[str]]:
        """Probe LM Studio for model list. Returns list of model IDs or None
        if unreachable."""
        try:
            req = urllib.request.Request(
                f"http://{ip}:{LMSTUDIO_PORT}/v1/models",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode())
                models = data.get("data") or []
                return [m["id"] for m in models if isinstance(m, dict) and m.get("id")]
        except Exception:
            return None

    @staticmethod
    def _probe_script() -> bool:
        try:
            subprocess.run("true", shell=True, capture_output=True, timeout=5)
            return True
        except Exception:
            return False

    def _pick_node(self, required: list[str], preferred_model: str = "", exclude: set[str] | None = None) -> Optional[dict]:
        """Pick the best node for a task. If a specific model is requested,
        use benchmark-based routing to pick the optimal node. Otherwise use
        composite benchmark scores (TPS * eval) across loaded models as the
        effective weight for weighted round-robin. Skips nodes that haven't
        been seen recently. ``exclude`` is a set of hostnames/IPs to skip."""
        with self._node_lock:
            candidates = list(self._nodes)
        now = time.time()
        exclude = exclude or set()
        alive = [
            n for n in candidates
            if (now - n.get("last_seen", 0)) < NODE_HEALTH_TTL
            and n.get("hostname", n.get("ip", "")) not in exclude
        ]
        matched = []
        for n in alive:
            caps = set(n.get("capabilities") or [])
            if not caps.issuperset(required):
                continue
            hn = n.get("hostname", n.get("ip", "?"))
            w = max(1, n.get("weight", 1))
            if self._node_inflight.get(hn, 0) >= w:
                continue
            matched.append(n)

        if not matched:
            return None

        # If a specific model is requested, use benchmark-based routing
        if preferred_model and "llm" in required:
            model_lower = preferred_model.lower()
            best_node_name = self._model_best_node.get(model_lower)
            fallbacks = self._model_fallbacks.get(model_lower, [])

            if best_node_name:
                for n in matched:
                    if n.get("hostname", "").lower() == best_node_name:
                        node_models = [m.lower() for m in n.get("loaded_models", [])]
                        if any(model_lower in m for m in node_models):
                            routing_info = self._model_routing.get(model_lower, {})
                            logger.info(
                                "fleet executor: routing %s to best node %s (TPS: %.1f, eval: %.2f, composite: %.1f)",
                                preferred_model, best_node_name,
                                routing_info.get("best_tps", 0),
                                routing_info.get("best_eval", 0),
                                routing_info.get("best_composite", 0)
                            )
                            return n

            for fallback_name in fallbacks:
                for n in matched:
                    if n.get("hostname", "").lower() == fallback_name:
                        node_models = [m.lower() for m in n.get("loaded_models", [])]
                        if any(model_lower in m for m in node_models):
                            logger.info(
                                "fleet executor: routing %s to fallback node %s",
                                preferred_model, fallback_name
                            )
                            return n

            for n in matched:
                node_models = [m.lower() for m in n.get("loaded_models", [])]
                if any(model_lower in m for m in node_models):
                    return n

            for n in matched:
                node_models_lower = [m.lower() for m in n.get("loaded_models", [])]
                if any(model_lower in m for m in node_models_lower):
                    return n

        # Generic LLM task (no specific model): use composite benchmark scores
        # as effective weights. Compute each node's best composite score across
        # its loaded models. Nodes with no benchmark data fall back to configured weight.
        routing = _get_routing()
        node_scores: dict[str, float] = {}
        for n in matched:
            hn = n.get("hostname", n.get("ip", "?"))
            best_composite = 0.0
            for model in n.get("loaded_models", []):
                perf = routing.get_model_perf(hn, model)
                if perf:
                    tps = perf.get("tps_med", 0) or 0
                    eval_score = perf.get("eval_score", 0) or 0
                    if tps > 0:
                        composite = tps * (eval_score + 0.1)
                        if composite > best_composite:
                            best_composite = composite
            if best_composite > 0:
                node_scores[hn] = best_composite
            else:
                node_scores[hn] = float(n.get("weight", 1))

        # Weighted round-robin using composite scores as weights
        best: Optional[dict] = None
        best_score = float("inf")
        for n in matched:
            hn = n.get("hostname", n.get("ip", "?"))
            effective_weight = max(1.0, node_scores.get(hn, float(n.get("weight", 1))))
            picked = self._pick_count.get(hn, 0)
            score = picked / effective_weight
            if score < best_score:
                best_score = score
                best = n
        if best:
            hn = best.get("hostname", best.get("ip", "?"))
            self._pick_count[hn] = self._pick_count.get(hn, 0) + 1
            if sum(self._pick_count.values()) > len(matched) * 20:
                for k in list(self._pick_count.keys()):
                    self._pick_count[k] = self._pick_count[k] // 2
            return best
        return matched[self._rr_index]

    @staticmethod
    def _pick_fast_model(models: list[str]) -> str:
        if not models:
            return ""
        def size_rank(m: str) -> int:
            ml = m.lower()
            for big in ("35b", "32b", "30b", "27b", "27-", "70b", "72b"):
                if big in ml:
                    return 3
            for med in ("14b", "13b", "12b", "9b", "8b", "7b", "3b", "4b"):
                if med in ml:
                    return 1
            return 2
        return sorted(models, key=size_rank)[0]

    def _pick_best_model(self, node: dict) -> str:
        """Pick the best model for a node using benchmark data, respecting hardware constraints."""
        models = node.get("loaded_models", [])
        if not models:
            return ""
        hostname = node.get("hostname", "")
        routing = _get_routing()

        # Score each model by TPS from benchmark data, filtered by hardware fit
        best_model = ""
        best_score = -1.0
        for model in models:
            # Check if model fits on this node's hardware
            fit = routing.check_model_fit(hostname, model)
            if not fit.get("fits", True):  # Default True if no spec data
                logger.debug("fleet executor: model %s does not fit on %s: %s", model, hostname, fit.get("reason"))
                continue
            
            perf = routing.get_model_perf(hostname, model)
            if perf:
                # Prefer models with high TPS and good eval score
                tps = perf.get("tps_med", 0)
                eval_score = perf.get("eval_score", 0)
                # Combined score: TPS * eval_score (0-1 range)
                score = tps * (eval_score + 0.1)  # +0.1 so even 0 eval gets some weight
                if score > best_score:
                    best_score = score
                    best_model = model

        if best_model:
            return best_model

        # Fallback: pick smallest model that fits
        for model in sorted(models, key=lambda m: routing._estimate_model_size(m)):
            fit = routing.check_model_fit(hostname, model)
            if fit.get("fits", True):
                return model

        # Last resort: size-based heuristic
        return self._pick_fast_model(models)

    def _call_lmstudio(self, node: dict, messages: list[dict], model: str = "", timeout: int = 300) -> dict:
        ip = node.get("ip", "127.0.0.1")
        hostname = node.get("hostname", ip)
        url = f"http://{ip}:{LMSTUDIO_PORT}/v1/chat/completions"
        payload = {
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        if model:
            payload["model"] = model
        elif node.get("loaded_models"):
            payload["model"] = self._pick_best_model(node)

        logger.info(
            "fleet executor: calling LM Studio on %s model=%s",
            hostname, payload.get("model", "default"),
        )

        # Acquire per-node semaphore to limit concurrent LM Studio requests
        node_sem = self._node_semaphores.get(hostname)
        if node_sem:
            node_sem.acquire()
        try:
            st, body = _http("POST", url, data=payload, timeout=timeout)
        finally:
            if node_sem:
                node_sem.release()

        # If the model isn't actually loaded (LM Studio library entry but
        # not in GPU), fall back to the default model / no model specified.
        if st == 400 and isinstance(body, dict):
            err_msg = (
                body.get("error", {})
                .get("message", "")
                if isinstance(body.get("error"), dict)
                else str(body.get("error", ""))
            )
            logger.debug("fleet executor: LM Studio %s error: %s", hostname, err_msg)
            # Handle "Failed to load model" - retry without specifying a model
            if "Failed to load model" in err_msg and payload.get("model"):
                logger.warning(
                    "fleet executor: model '%s' not loaded on %s, retrying with default",
                    payload["model"], hostname,
                )
                del payload["model"]
                if node_sem:
                    node_sem.acquire()
                try:
                    st, body = _http("POST", url, data=payload, timeout=timeout)
                finally:
                    if node_sem:
                        node_sem.release()
                # After removing model, check for "Multiple models" on retry
                if st == 400 and isinstance(body, dict):
                    err_msg = (
                        body.get("error", {})
                        .get("message", "")
                        if isinstance(body.get("error"), dict)
                        else str(body.get("error", ""))
                    )
            # Handle "Multiple models are loaded" - retry with a specific fast model
            if "Multiple models are loaded" in err_msg and not payload.get("model"):
                fast_model = self._pick_fast_model(node.get("loaded_models", []))
                if fast_model:
                    logger.warning(
                        "fleet executor: multiple models loaded on %s, retrying with %s",
                        hostname, fast_model,
                    )
                    payload["model"] = fast_model
                    if node_sem:
                        node_sem.acquire()
                    try:
                        st, body = _http("POST", url, data=payload, timeout=timeout)
                    finally:
                        if node_sem:
                            node_sem.release()

        if st != 200:
            logger.warning("fleet executor: LM Studio %s returned %s", hostname, st)
            return {"error": body, "status_code": st, "exit_code": 1}

        choice = body.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        usage = body.get("usage", {})
        completion_tokens = usage.get("completion_tokens", 0)

        # Validate response quality
        validation = self._validate_response(content, completion_tokens)
        if not validation["valid"]:
            logger.warning("fleet executor: LM Studio %s response validation failed: %s", hostname, validation["reason"])
            return {"error": validation["reason"], "status_code": st, "exit_code": 1}

        return {
            "content": content,
            "model": body.get("model", payload.get("model", "")),
            "usage": usage,
            "node": hostname,
            "exit_code": 0,
            "validation": validation,
        }

    def _validate_response(self, content: str, completion_tokens: int) -> dict:
        """Validate LLM response quality. Returns dict with valid:bool, reason:str, metrics."""
        if not content or not content.strip():
            return {"valid": False, "reason": "empty_response", "metrics": {}}

        stripped = content.strip()
        if len(stripped) < 10:
            return {"valid": False, "reason": "too_short", "metrics": {"length": len(stripped)}}

        # Check for common refusal/error patterns
        refusal_patterns = [
            "i cannot", "i can't", "i'm unable", "i am unable",
            "as an ai language model", "i don't have", "i do not have",
            "i apologize", "i'm sorry", "i am sorry",
            "cannot fulfill", "unable to fulfill", "refuse to",
            "error:", "exception:", "traceback",
            "i don't know", "i do not know", "not sure",
        ]
        lower = stripped.lower()
        for pattern in refusal_patterns:
            if pattern in lower[:200]:  # Check first 200 chars
                return {"valid": False, "reason": f"refusal_pattern:{pattern}", "metrics": {}}

        # Check for minimum token usage (avoid degenerate responses)
        if completion_tokens > 0 and completion_tokens < 5:
            return {"valid": False, "reason": "too_few_tokens", "metrics": {"completion_tokens": completion_tokens}}

        # For kg_insight style tasks, check for structured content indicators
        # (these tasks should produce analysis/insights, not just short answers)
        insight_indicators = ["analysis", "insight", "summary", "finding", "conclusion", "recommendation",
                             "key point", "observation", "implication", "pattern", "trend"]
        has_insight_structure = any(ind in lower for ind in insight_indicators)

        return {
            "valid": True,
            "reason": "ok",
            "metrics": {
                "length": len(stripped),
                "completion_tokens": completion_tokens,
                "has_insight_structure": has_insight_structure,
            },
        }

    def _pick_and_reserve_node(self, required: list[str], preferred_model: str = "", exclude: set[str] | None = None) -> Optional[dict]:
        """Atomically pick a node and increment its inflight counter.
        Returns the node dict or None if no suitable node available.

        NOTE: _pick_node() acquires _node_lock itself, so we must NOT hold
        the lock here — doing so would deadlock on the non-reentrant lock.
        We only take the lock around the inflight counter mutation.
        """
        node = self._pick_node(required, preferred_model, exclude)
        if node:
            with self._node_lock:
                hn = node.get("hostname", node.get("ip", "?"))
                self._node_inflight[hn] = self._node_inflight.get(hn, 0) + 1
        return node

    def _release_node(self, hostname: str) -> None:
        """Decrement inflight counter for a node."""
        with self._node_lock:
            if hostname in self._node_inflight:
                self._node_inflight[hostname] = max(0, self._node_inflight[hostname] - 1)

    def _execute_task(self, task: dict, reserved_node: dict | None = None) -> dict:
        payload = task.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        req_caps = task.get("required_capabilities") or task.get("required_capabilities_plain") or ["script"]

        if "llm" in req_caps:
            messages = payload.get("messages") or [
                {"role": "user", "content": payload.get("prompt", payload.get("command", ""))}
            ]
            model = payload.get("model", "")
            tried: set[str] = set()

            # First attempt: use reserved node if provided
            if reserved_node:
                hn = reserved_node.get("hostname", reserved_node.get("ip", "?"))
                tried.add(hn)
                try:
                    start = time.time()
                    result = self._call_lmstudio(reserved_node, messages, model)
                    elapsed = time.time() - start
                finally:
                    self._release_node(hn)
                result["node"] = hn
                if result.get("exit_code", 1) == 0:
                    old = self._node_latency.get(hn, elapsed)
                    self._node_latency[hn] = old * 0.7 + elapsed * 0.3
                    return result
                logger.warning(
                    "fleet executor: reserved node %s failed (model=%s), trying fallback",
                    hn, model,
                )

            # Pass 1: nodes that have the requested model loaded.
            for _ in range(min(4, len(self._nodes) + 1)):
                node = self._pick_and_reserve_node(["llm"], preferred_model=model, exclude=tried)
                if not node:
                    break
                hn = node.get("hostname", node.get("ip", "?"))
                tried.add(hn)
                try:
                    start = time.time()
                    result = self._call_lmstudio(node, messages, model)
                    elapsed = time.time() - start
                finally:
                    self._release_node(hn)
                result["node"] = hn
                if result.get("exit_code", 1) == 0:
                    old = self._node_latency.get(hn, elapsed)
                    self._node_latency[hn] = old * 0.7 + elapsed * 0.3
                    return result
                logger.warning(
                    "fleet executor: node %s failed (model=%s), trying next",
                    hn, model,
                )

            # Pass 2: fall back to ANY LLM node with no model hint.
            for _ in range(min(4, len(self._nodes) + 1)):
                node = self._pick_and_reserve_node(["llm"], preferred_model="", exclude=tried)
                if not node:
                    break
                hn = node.get("hostname", node.get("ip", "?"))
                tried.add(hn)
                try:
                    start = time.time()
                    result = self._call_lmstudio(node, messages, "")
                    elapsed = time.time() - start
                finally:
                    self._release_node(hn)
                result["node"] = hn
                if result.get("exit_code", 1) == 0:
                    old = self._node_latency.get(hn, elapsed)
                    self._node_latency[hn] = old * 0.7 + elapsed * 0.3
                    return result
                logger.warning(
                    "fleet executor: fallback node %s failed, trying next",
                    hn,
                )

            return {"error": "all llm nodes failed", "exit_code": 1}

        if "script" in req_caps:
            command = payload.get("command") or payload.get("prompt", "")
            result = self._run_script(command)
            return result

        return {"error": f"unhandled capabilities: {req_caps}", "exit_code": 1}

    def _run_script(self, command: str, timeout: int = 120) -> dict:
        try:
            r = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return {"stdout": r.stdout, "stderr": r.stderr, "exit_code": r.returncode}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "timeout", "exit_code": -1}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}

    def _process_tasks(self) -> None:
        self._refresh_nodes()

        # Fetch a full page of LLM tasks at once (the API returns distinct
        # READY tasks sorted by created_at). Spawn one worker thread per task
        # up to the LLM semaphore cap. Repeats the fetch until the semaphore
        # is saturated or no READY tasks remain, so the fleet stays busy
        # instead of dispatching one fixed page per poll.
        seen_ids: set[str] = set()
        while True:
            if self._llm_sem.acquire(blocking=False):
                st, body = _http(
                    "GET",
                    f"{ASSISTX_URL}/api/agent/tasks?status=READY&capabilities=llm&limit={MAX_CONCURRENT_LLM}",
                    timeout=15,
                )
                if st != 200:
                    self._llm_sem.release()
                    break
                rows = (body.get("items") if isinstance(body, dict) else body) or []
                if not isinstance(rows, list):
                    rows = []
                fresh = [r for r in rows if r.get("id") not in seen_ids]
                if not fresh:
                    self._llm_sem.release()
                    break
                for row in fresh:
                    seen_ids.add(row.get("id"))
                    # Atomically pick and reserve a node for this LLM task
                    payload_raw = row.get("payload_json") or row.get("payload") or "{}"
                    try:
                        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                    except Exception:
                        payload = {}
                    model_hint = payload.get("model", "")
                    raw_caps = row.get("required_capabilities") or []
                    if isinstance(raw_caps, str):
                        try:
                            req_caps = json.loads(raw_caps)
                        except Exception:
                            req_caps = ["llm"]
                    else:
                        req_caps = list(raw_caps) if raw_caps else ["llm"]
                    reserved_node = self._pick_and_reserve_node(req_caps, preferred_model=model_hint)
                    if not reserved_node:
                        # No node available; release semaphore and re-queue task implicitly
                        self._llm_sem.release()
                        logger.warning("fleet executor: no node available for task %s, releasing semaphore", row.get("id"))
                        break
                    t = threading.Thread(
                        target=self._handle_one,
                        args=(row, "llm", reserved_node),
                        daemon=True,
                    )
                    t.start()
            else:
                break

        # Launch script tasks — they use the script semaphore pool.
        script_seen: set[str] = set()
        while True:
            if self._script_sem.acquire(blocking=False):
                st, body = _http(
                    "GET",
                    f"{ASSISTX_URL}/api/agent/tasks?status=READY&limit={MAX_CONCURRENT_SCRIPT}",
                    timeout=15,
                )
                if st != 200:
                    self._script_sem.release()
                    break
                rows = (body.get("items") if isinstance(body, dict) else body) or []
                if not isinstance(rows, list):
                    rows = []
                fresh = [r for r in rows if r.get("id") not in script_seen]
                if not fresh:
                    self._script_sem.release()
                    break
                for row in fresh:
                    script_seen.add(row.get("id"))
                    t = threading.Thread(
                        target=self._handle_one,
                        args=(row, "script"),
                        daemon=True,
                    )
                    t.start()
            else:
                break

    def _handle_one(self, row: dict, kind: str = "script", reserved_node: dict | None = None) -> None:
        try:
            self._do_handle(row, reserved_node)
        finally:
            if kind == "llm":
                self._llm_sem.release()
            else:
                self._script_sem.release()

    def _do_handle(self, row: dict, reserved_node: dict | None = None) -> None:
        """Execute a single task end-to-end: claim, run, complete."""
        task_id = row["id"]
        payload_raw = row.get("payload_json") or row.get("payload") or "{}"
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except Exception:
            payload = {}
        raw_caps = row.get("required_capabilities") or []
        if isinstance(raw_caps, str):
            try:
                req_caps = json.loads(raw_caps)
            except Exception:
                req_caps = ["script"]
        else:
            req_caps = list(raw_caps) if raw_caps else ["script"]

        logger.info(
            "fleet executor: %s (%s) caps=%s",
            task_id, row.get("title", "?"), req_caps,
        )

        ik = f"fleet-exec/{task_id}"
        attempt_key = f"{ik}/{int(time.time())}"

        st, claim = _http(
            "POST",
            f"{ASSISTX_URL}/api/tasks/{task_id}/claim",
            data={"agent_id": "fleet-executor", "idempotency_key": attempt_key},
            timeout=15,
        )
        if st != 200:
            logger.info("fleet executor: claim %s failed (%s)", task_id, st)
            return
        if not claim.get("claimed", False):
            return

        task_dict = {"id": task_id, "payload": payload, "required_capabilities": req_caps}
        result = self._execute_task(task_dict, reserved_node)

        st, _ = _http(
            "POST",
            f"{ASSISTX_URL}/api/tasks/{task_id}/complete",
            data={
                "agent_id": "fleet-executor",
                "status": "DONE" if result.get("exit_code", 0) == 0 else "FAILED",
                "result": result,
                "idempotency_key": f"fleet-exec/complete/{task_id}/{int(time.time())}",
            },
            timeout=15,
        )
        if st == 200:
            logger.info("fleet executor: %s -> DONE  node=%s", task_id, result.get("node", "local"))
        else:
            logger.warning("fleet executor: %s complete failed (%s)", task_id, st)

    def run_once(self) -> None:
        """One poll + process cycle. For testing or manual trigger."""
        self._refresh_nodes()
        self._process_tasks()


def _start_executor_loop() -> None:
    """Start the daemon background thread."""

    def _loop() -> None:
        global _fleet_executor_instance
        executor = FleetExecutor()
        _fleet_executor_instance = executor
        time.sleep(15)
        logger.info("fleet executor: starting poll loop (every %ss)", EXECUTOR_INTERVAL)
        while True:
            try:
                executor._refresh_nodes()
                executor._process_tasks()
            except Exception as e:
                logger.warning("fleet executor: cycle error: %s", e)
            time.sleep(EXECUTOR_INTERVAL)

    t = threading.Thread(target=_loop, name="fleet-executor", daemon=True)
    t.start()
    logger.info("fleet executor: daemon thread started")


def get_fleet_executor() -> Optional[FleetExecutor]:
    """Get the running fleet executor instance for API access."""
    return _fleet_executor_instance