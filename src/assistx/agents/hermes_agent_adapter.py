from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import yaml

logger = logging.getLogger(__name__)

ASSISTX_URL = os.getenv("ASSISTX_URL", "http://localhost:8000")
ASSISTX_USER = os.getenv("ASSISTX_USER", "admin")
ASSISTX_PASS = os.getenv("ASSISTX_PASS", "change-me")
AGENT_ID = os.getenv("HERMES_AGENT_ID", "hermes-local")
AGENT_CAPABILITIES = os.getenv("HERMES_AGENT_CAPABILITIES", "terminal,file,code_execution,web").split(",")
POLL_INTERVAL = int(os.getenv("HERMES_POLL_INTERVAL", "15"))
HERMES_BIN = os.getenv("HERMES_BIN", "hermes")
HERMES_PROVIDER = os.getenv("HERMES_PROVIDER", "assistx-router")
HERMES_MODEL = os.getenv("HERMES_MODEL", "groq.llama-3.1-8b-instant")
HERMES_TIMEOUT = int(os.getenv("HERMES_TASK_TIMEOUT", "300"))
HERMES_SMOKE_TIMEOUT = int(os.getenv("HERMES_SMOKE_TIMEOUT", "120"))
MAX_TASKS_PER_LOOP = int(os.getenv("HERMES_MAX_TASKS_PER_LOOP", "3"))
LEASE_SECONDS = int(os.getenv("HERMES_LEASE_SECONDS", "900"))
PROFILES_PATH = os.getenv("HERMES_PROFILES_PATH", "/root/.hermes/profiles.yaml")
PROFILES_DEFAULT = os.getenv("HERMES_PROFILES_DEFAULT", "exec")
HERMES_TOOLSETS = os.getenv("HERMES_TOOLSETS", "terminal,file,code_execution,web,memory")
EVAL_PATH = os.getenv("HERMES_EVAL_PATH", "/root/knowledge/model-profiles.json")
KNOWLEDGE_ROOT = os.getenv("HERMES_KNOWLEDGE_ROOT", os.path.dirname(EVAL_PATH))
EVAL_LOCK = threading.Lock()
SELFTASK_BULK_MODELS = [m for m in os.getenv("HERMES_SELFTASK_BULK_MODELS", "").split(",") if m]
SELFTASK_BULK_TIMEOUT = int(os.getenv("HERMES_SELFTASK_BULK_TIMEOUT", "600"))
SELFTASK_INTERVAL = int(os.getenv("HERMES_SELFTASK_INTERVAL", "3"))
# Background self-tasks are pure text-in/text-out harvesting; we call the router's
# chat API directly (no Hermes CLI) so we can cap max_tokens. Small/tiny models are
# slow and Hermes sends no token cap, which made them generate unbounded and time out.
SELFTASK_MAX_TOKENS = int(os.getenv("HERMES_SELFTASK_MAX_TOKENS", "450"))
ROUTER_CHAT_URL = os.getenv("HERMES_ROUTER_CHAT_URL", "http://host.docker.internal:8088/v1/chat/completions")

MODEL_PROFILE_DEFAULTS = {
    "reasoning-large": {
        "tier": "reasoning-large",
        "profile": "reasoning-large",
        "model": "ornith-1.0-35b",
        "provider": "assistx-router",
        "context_length": 131072,
    },
    "reasoning-mid": {
        "tier": "reasoning-mid",
        "profile": "reasoning-mid",
        "model": "orinth-1.0-9b",
        "provider": "assistx-router",
        "context_length": 32768,
    },
    "tool-small": {
        "tier": "tool-small",
        "profile": "tool-small",
        "model": "refinedtoolcallv5-3b",
        "provider": "assistx-router",
        "context_length": 131072,
    },
    "compress-tiny": {
        "tier": "compress-tiny",
        "profile": "compress-tiny",
        "model": "qwen3.5-0.8b-claude-4.6-opus-reasoning-distilled",
        "provider": "assistx-router",
        "context_length": 65536,
    },
    "cpu-micro": {
        "tier": "cpu-micro",
        "profile": "cpu-micro",
        "model": "liquid/lfm2.5-1.2b",
        "provider": "assistx-router",
        "context_length": 4096,
    },
}

_TRIGGER_KEYWORDS = [
    ("auto-router", ["auto-router", "auto_router", "llm router", "model placement", "lm studio"]),
    ("auto-assign", ["auto-assign", "auto_assign", "assignment governor", "auto-assign", "heartbeat"]),
    ("auto-ingest", ["auto-ingest", "auto_ingest", "ingestion", "lyrics", "diarize", "diarization"]),
    ("auto-insurance", ["auto-insurance", "auto_insurance", "insurance", "claims", "billing"]),
    ("auto-assist", ["auto-assist", "auto_assist", "assistx", "neo4j brain", "hermes-agent-adapter"]),
    ("hermes-agent", ["hermes-agent", "hermes_agent", "hermes framework", "hermes profile", "toolset"]),
]

_MODEL_TIER_KEYWORDS = [
    ("compress-tiny", ["summar", "compress", "triage", "draft", "condense", "abstract"]),
    ("cpu-micro", ["classify", "extract", "background", "cheap", "categorize", "tag", "label"]),
    ("tool-small", ["fix", "typo", "script", "tool", "rename", "small edit", "one-liner"]),
    ("reasoning-mid", ["edit", "implement", "feature", "add", "update", "refactor"]),
    ("reasoning-large", ["architect", "design", "complex", "review", "deep", "migrate", "investigate"]),
]
_MODEL_TIER_ORDER = ["compress-tiny", "cpu-micro", "tool-small", "reasoning-mid", "reasoning-large"]

_SELFTASK_ARCHETYPES = ["bulk_summarize", "session_compress", "corpus_extract", "triage", "ideation"]

# Artifact filename each self-task archetype writes into KNOWLEDGE_ROOT.
_SELFTASK_TARGETS = {
    "bulk_summarize": "SUMMARY.md",
    "session_compress": "recap.md",
    "corpus_extract": "extracted_facts.md",
    "triage": "TRIAGE.md",
    "ideation": "IDEAS.md",
}


def _safe_model_dir(model: Optional[str] = None) -> str:
    """Filesystem-safe subdirectory name for a model (handles '/' in ids)."""
    return (model or "unknown").replace("/", "_")


# ---------------------------------------------------------------------------
# Eval registry (shared, host-writable ~/knowledge/model-profiles.json)
# ---------------------------------------------------------------------------
def load_eval() -> Dict[str, Any]:
    with EVAL_LOCK:
        try:
            with open(EVAL_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    data.setdefault("models", {})
                    return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {"models": {}}


def save_eval(data: Dict[str, Any]) -> None:
    with EVAL_LOCK:
        directory = os.path.dirname(EVAL_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = f"{EVAL_PATH}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, EVAL_PATH)
        except OSError as e:
            logger.warning("Failed to persist eval registry %s: %s", EVAL_PATH, e)


def ensure_model_env(model: Optional[str] = None) -> Optional[str]:
    """A model's first task: configure its shared workspace under ~/knowledge.

    Idempotent. Creates ~/knowledge/<model>/ and writes an ENV note recording the
    model's tier/nodes, then flags the environment as configured so future runs
    can rely on it. The workspace is shared across the whole swarm.
    """
    if not model:
        return None
    data = load_eval()
    m = data["models"].setdefault(model, {"environment_configured": False, "tasks": {}})
    m.setdefault("tier", "unknown")
    m.setdefault("nodes", [])
    ws = f"/root/knowledge/{_safe_model_dir(model)}"
    try:
        os.makedirs(ws, exist_ok=True)
        if not m.get("environment_configured"):
            note = os.path.join(ws, "ENV.md")
            nodes = ", ".join(m.get("nodes", [])) or "n/a"
            try:
                with open(note, "w", encoding="utf-8") as fh:
                    fh.write(f"# Environment for model `{model}`\n\n")
                    fh.write(f"- Tier: {m.get('tier')}\n")
                    fh.write(f"- Nodes: {nodes}\n")
                    fh.write("- Shared knowledge root: ~/knowledge (abs /media/scott/SSD_4TB/knowledge)\n")
                    fh.write("- This directory is the model's scratch/notes space, shared across the swarm.\n")
                    fh.write("- Configured by hermes-agent-adapter bootstrap at first use.\n")
                m["environment_configured"] = True
                m["workspace"] = ws
                save_eval(data)
                logger.info("Configured shared environment for model %s at %s", model, ws)
            except OSError as e:
                logger.warning("Could not write env note for %s: %s", model, e)
        return ws
    except OSError as e:
        logger.warning("Could not create workspace for %s: %s", model, e)
        return "/root/knowledge"


TRIVIAL_OUTPUT_PATTERNS = [
    re.compile(r"done\s*[-–]\s*i[’']?ve completed", re.I),
    re.compile(r"i(?: have|'ve) completed that for you", re.I),
    re.compile(r"let me know if you need (?:any|anything)", re.I),
    re.compile(r"^done\.?$", re.I),
    re.compile(r"i'?ve (?:got it|taken care of it)", re.I),
]


def is_trivial_output(output: str) -> bool:
    """Heuristic: did the agent actually do work, or just claim 'done'?"""
    if not output or len(output.strip()) < 15:
        return True
    return any(p.search(output) for p in TRIVIAL_OUTPUT_PATTERNS)


def _classify_error(error: Optional[str]) -> str:
    e = (error or "").lower()
    if "timeout" in e:
        return "timeout"
    if "exit_code" in e:
        return "exit"
    if not e:
        return "empty"
    return "other"


def record_task_eval(
    model: Optional[str],
    category: Optional[str],
    success: bool,
    elapsed: float,
    error: Optional[str] = None,
    trivial: bool = False,
) -> None:
    """Record a task outcome.

    ``success`` here means *substantive* completion: hermes exited cleanly AND the
    output was non-trivial. This keeps the eval success_rate honest instead of
    counting "Done - I've completed that for you." as a win. Failure reasons and a
    short ring buffer of recent failures are persisted for the watchdog.
    """
    if not model or not category:
        return
    data = load_eval()
    m = data["models"].setdefault(model, {"environment_configured": False, "tasks": {}})
    m.setdefault("tier", "unknown")
    m.setdefault("nodes", [])
    t = m["tasks"].setdefault(
        category, {"runs": 0, "success": 0, "avg_seconds": 0.0, "success_rate": 0.0}
    )
    t["runs"] += 1
    if success:
        t["success"] += 1
        t.pop("last_error", None)
        t.pop("error_kind", None)
    else:
        now = datetime.now(timezone.utc).isoformat()
        t["last_failure"] = now
        if error:
            t["last_error"] = str(error)[:200]
            t["error_kind"] = _classify_error(error)
            rb = t.setdefault("recent_failures", [])
            rb.append({"ts": now, "error": str(error)[:160], "kind": _classify_error(error)})
            t["recent_failures"] = rb[-10:]
    if trivial:
        t["trivial"] = t.get("trivial", 0) + 1
    prev_avg = float(t.get("avg_seconds", 0.0) or 0.0)
    new_avg = (prev_avg * (t["runs"] - 1) + max(float(elapsed or 0), 0.0)) / t["runs"]
    t["avg_seconds"] = round(new_avg, 1)
    t["success_rate"] = round(t["success"] / t["runs"], 2) if t["runs"] else 0.0
    save_eval(data)


def get_model_prompt(model: Optional[str]) -> str:
    """Per-model performance prompt, tuned from observed eval data."""
    data = load_eval()
    m = data.get("models", {}).get(model or "", {})
    base = m.get("prompt", "You are a helpful agent operating in the local swarm.")
    tasks = m.get("tasks", {})
    weak = sorted(
        tasks.items(),
        key=lambda x: (x[1].get("success_rate", 1.0), -x[1].get("runs", 0)),
    )[:3]
    lines = [f"[Model performance profile — {model}]", base]
    if weak:
        summary = "; ".join(
            f"{cat}: {info.get('success_rate', 0)*100:.0f}% over {info.get('runs',0)} runs"
            for cat, info in weak
        )
        lines.append(f"[Observed weak spots] {summary}")
    lines.append(
        f"[Shared workspace] Your environment + shared knowledge live at ~/knowledge "
        f"(abs /media/scott/SSD_4TB/knowledge). Model notes: ~/knowledge/{_safe_model_dir(model)}/."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Profiles (tiers x triggers) + routing
# ---------------------------------------------------------------------------
def load_profiles() -> Dict[str, Any]:
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as fh:
            reg = yaml.safe_load(fh) or {}
    except (FileNotFoundError, yaml.YAMLError):
        reg = {}
    reg.setdefault("models", {})
    reg.setdefault("triggers", {})
    for name, defaults in MODEL_PROFILE_DEFAULTS.items():
        reg["models"].setdefault(name, dict(defaults))
    reg.setdefault("default_model", "reasoning-large")
    reg.setdefault("default_trigger", PROFILES_DEFAULT)
    return reg


def get_model_tier(name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resolve a model-size tier (which model + node executes the tool calls)."""
    reg = load_profiles()
    tiers = reg.get("models", {})
    if name and name in tiers:
        return tiers[name]
    default = reg.get("default_model", PROFILES_DEFAULT)
    return tiers.get(default)


def get_trigger(name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resolve a swarm repo/topic trigger (which directories / files to use)."""
    reg = load_profiles()
    triggers = reg.get("triggers", {})
    if name and name in triggers:
        return triggers[name]
    default = reg.get("default_trigger", PROFILES_DEFAULT)
    return triggers.get(default)


def classify_trigger(task: Optional[Dict[str, Any]] = None, payload: Optional[Dict[str, Any]] = None) -> Optional[str]:
    t = task or {}
    title = str(t.get("title", "") or t.get("kind", ""))
    desc = str(t.get("description", "") or t.get("text", ""))
    repo = str((payload or {}).get("repo", "") or t.get("repo", ""))
    text = f"{title} {desc} {repo}".lower()
    best = None
    best_score = 0
    for trigger, kws in _TRIGGER_KEYWORDS:
        score = sum(1 for kw in kws if kw in text)
        if score > best_score:
            best_score = score
            best = trigger
    return best


def classify_model_tier(task: Optional[Dict[str, Any]] = None, payload: Optional[Dict[str, Any]] = None) -> str:
    t = task or {}
    title = str(t.get("title", "") or t.get("kind", ""))
    desc = str(t.get("description", "") or t.get("text", ""))
    text = f"{title} {desc}".lower()
    scores = {}
    for tier, kws in _MODEL_TIER_KEYWORDS:
        s = sum(1 for kw in kws if kw in text)
        if s:
            scores[tier] = s
    if scores:
        return max(scores, key=lambda tier: (scores[tier], -_MODEL_TIER_ORDER.index(tier)))
    return _route_by_shape(task, payload)


def _route_by_shape(task: Optional[Dict[str, Any]] = None, payload: Optional[Dict[str, Any]] = None) -> str:
    """Fallback router: stable per-task hash distributes across the capable tiers."""
    seed = str((task or {}).get("id", "") or hash(str(payload)))
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    capable = [t for t in _MODEL_TIER_ORDER if t != "cpu-micro"]
    return capable[h % len(capable)]


def select_tier_model(tier: Optional[str], seed: Optional[str] = None) -> Optional[str]:
    """Pick the concrete model for a tier, rotating across candidates by seed."""
    t = get_model_tier(tier)
    if not t:
        return None
    model = t.get("model")
    cands = [c for c in (t.get("candidates") or []) if c != model]
    pool = [model] + cands if model else cands
    if not pool:
        return None
    if seed is None:
        return pool[0]
    h = int(hashlib.sha256(str(seed).encode()).hexdigest(), 16)
    return pool[h % len(pool)]


def task_category(task: Optional[Dict[str, Any]] = None, payload: Optional[Dict[str, Any]] = None) -> str:
    t = task or {}
    kind = str(t.get("kind") or t.get("type") or "task")
    kind_l = kind.lower()
    if kind_l in ("self", "bulk", "selftask", "background") or "self" in kind_l or "bulk" in kind_l:
        arch = str((payload or {}).get("archetype") or t.get("archetype") or "general")
        return f"self:{arch}"
    trigger = classify_trigger(task, payload)
    repo = trigger or "other"
    return f"{kind}:{repo}"


# ---------------------------------------------------------------------------
# AssistX client
# ---------------------------------------------------------------------------
class AssistXClient:
    def __init__(
        self,
        base_url: str = ASSISTX_URL,
        username: str = ASSISTX_USER,
        password: str = ASSISTX_PASS,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password)

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", 30)
        resp = requests.request(method, url, auth=self.auth, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def poll_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        caps_param = "&".join(f"capabilities={c}" for c in AGENT_CAPABILITIES)
        result = self._request(
            "GET",
            f"/api/agent/tasks?status=READY&agent_id={AGENT_ID}&limit={limit}&{caps_param}",
        )
        return result.get("items", [])

    def claim_task(self, task_id: str, session_id: str) -> bool:
        try:
            self._request(
                "POST",
                f"/api/tasks/{task_id}/claim",
                json={
                    "agent_id": AGENT_ID,
                    "capabilities": AGENT_CAPABILITIES,
                    "session_id": session_id,
                },
            )
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                logger.info("Task %s already claimed by another agent", task_id)
                return False
            raise

    def get_context(self, task_id: str, query: str) -> Dict[str, Any]:
        try:
            result = self._request(
                "POST",
                "/api/brain/context",
                json={
                    "query": query,
                    "task_id": task_id,
                    "max_items": 20,
                    "include_sources": ["memory", "knowledge", "orchestration"],
                },
            )
            return result.get("context_packet", {})
        except requests.RequestException as e:
            logger.warning("Context lookup failed for task %s: %s", task_id, e)
            return {}

    def heartbeat(self, task_id: str, session_id: str, status: str = "RUNNING") -> None:
        try:
            self._request(
                "POST",
                f"/api/tasks/{task_id}/heartbeat",
                json={
                    "agent_id": AGENT_ID,
                    "status": status,
                    "session_id": session_id,
                },
            )
        except requests.RequestException as e:
            logger.warning("Heartbeat failed for task %s: %s", task_id, e)

    def complete_task(
        self,
        task_id: str,
        session_id: str,
        status: str,
        summary: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._request(
            "POST",
            f"/api/tasks/{task_id}/complete",
            json={
                "agent_id": AGENT_ID,
                "status": status,
                "summary": summary or "",
                "result": result or {},
                "session_id": session_id,
            },
        )

    def write_memory(
        self,
        kind: str,
        text: str,
        source: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        try:
            result = self._request(
                "POST",
                "/api/memory/items",
                json={
                    "kind": kind,
                    "text": text,
                    "source": source,
                    "task_id": task_id,
                    "session_id": session_id,
                },
            )
            return result.get("memory_item_id", "")
        except requests.RequestException as e:
            logger.warning("write_memory failed: %s", e)
            return ""

    def register_session(self, session_id: str, hermes_session_id: str) -> None:
        try:
            self._request(
                "POST",
                f"/api/sessions/{session_id}",
                json={
                    "hermes_session_id": hermes_session_id,
                    "platform": "linux",
                    "metadata": {"agent": AGENT_ID, "source": "hermes-agent-adapter"},
                },
            )
        except requests.RequestException as e:
            logger.warning("register_session failed: %s", e)


# ---------------------------------------------------------------------------
# Hermes execution
# ---------------------------------------------------------------------------
def run_hermes(
    prompt: str,
    timeout: int = HERMES_TIMEOUT,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    toolsets: Optional[str] = None,
) -> Dict[str, Any]:
    cmd = [
        HERMES_BIN,
        "chat",
        "-q", prompt,
        "--quiet",
        "--pass-session-id",
        "--max-turns", "20",
    ]
    if model:
        cmd += ["-m", model]
    if provider:
        cmd += ["--provider", provider]
    env = {**os.environ, "HERMES_ACCEPT_HOOKS": "1"}
    if toolsets:
        env["HERMES_TOOLSETS"] = toolsets
    logger.info("Running Hermes model=%s (timeout=%ds)", model or HERMES_MODEL, timeout)
    start = time.monotonic()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Hermes timed out after %ds", timeout)
        return {"success": False, "error": "timeout", "output": "", "session_id": None, "elapsed": timeout}
    except FileNotFoundError:
        logger.error("Hermes binary not found at %s", HERMES_BIN)
        return {"success": False, "error": "hermes_binary_not_found", "output": "", "session_id": None, "elapsed": 0.0}

    elapsed = time.monotonic() - start
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    session_id = None

    for line in stdout.splitlines():
        if "session_id:" in line or "Session ID:" in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                session_id = parts[1].strip()
                break

    if proc.returncode != 0:
        logger.warning("Hermes exit code %d (elapsed=%.1fs)", proc.returncode, elapsed)
        return {
            "success": False,
            "error": f"exit_code_{proc.returncode}",
            "output": stdout,
            "stderr": stderr,
            "session_id": session_id,
            "elapsed": elapsed,
        }

    logger.info("Hermes completed in %.1fs (session=%s)", elapsed, session_id or "N/A")
    return {
        "success": True,
        "output": stdout.strip(),
        "session_id": session_id,
        "elapsed": elapsed,
    }


def call_self_task_llm(prompt: str, model: str, max_tokens: int = SELFTASK_MAX_TOKENS,
                       timeout: int = 450) -> Dict[str, Any]:
    """Direct router chat call for background self-tasks.

    Hermes is not used here: small/tiny models are slow and Hermes sends no
    ``max_tokens`` cap, so they generate unbounded and time out. Calling the
    router directly lets us bound output length and avoid the tool-use loops
    that plague tiny models. Returns ``{"success", "output", "error", "elapsed"}``.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": False,
    }
    start = time.monotonic()
    try:
        resp = requests.post(ROUTER_CHAT_URL, json=payload, timeout=timeout)
        elapsed = time.monotonic() - start
        if resp.status_code != 200:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = resp.text[:200]
            logger.warning("Self-task LLM HTTP %d: %s", resp.status_code, detail)
            return {"success": False, "output": "", "error": f"http_{resp.status_code}", "elapsed": elapsed}
        data = resp.json()
        content = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        reasoning = (data.get("choices", [{}])[0].get("message", {}).get("reasoning_content") or "").strip()
        # Some tiny reasoning models inline <think>...</think> in content; strip it.
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        # Reasoning models may put the answer in content but fall back to reasoning if empty.
        final = content or reasoning
        return {"success": True, "output": final, "error": None, "elapsed": elapsed}
    except requests.RequestException as exc:
        elapsed = time.monotonic() - start
        logger.warning("Self-task LLM request failed: %s", exc)
        return {"success": False, "output": "", "error": f"request_error:{type(exc).__name__}", "elapsed": elapsed}


# ---------------------------------------------------------------------------
# Self-tasks (background harvesting on the small/bulk models)
# ---------------------------------------------------------------------------
def _self_artifact_present(model: Optional[str], since_ts: Optional[float] = None) -> Tuple[bool, str]:
    """Best-effort: did a knowledge artifact appear after a self-task run?

    A self-task's real work is the file it writes (SUMMARY.md, recap.md, ...),
    not the short chat confirmation it returns. So we treat the task as done
    only if a fresh non-template markdown appeared in the vault (or the model's
    own scratch dir grew beyond its ENV.md) since ``since_ts``.
    """
    model_dir = os.path.join(KNOWLEDGE_ROOT, (model or "unknown").replace("/", "_"))
    if os.path.isdir(model_dir):
        extras = [f for f in os.listdir(model_dir) if f != "ENV.md"]
        if extras:
            return True, os.path.relpath(model_dir, KNOWLEDGE_ROOT) + f": {extras[:3]}"
    if since_ts:
        cutoff = since_ts - 5
        # Only count TOP-LEVEL markdown (SUMMARY.md, recap.md, TRIAGE.md, ...).
        # Hermes writes its own session transcripts into subdirs (vault-workspace/
        # tasks), which must NOT count as the self-task's produced artifact.
        for f in os.listdir(KNOWLEDGE_ROOT):
            if not f.endswith(".md") or f in _KNOWLEDGE_TEMPLATE_FILES:
                continue
            fp = os.path.join(KNOWLEDGE_ROOT, f)
            if not os.path.isfile(fp):
                continue
            try:
                mtime = os.path.getmtime(fp)
            except OSError:
                continue
            if mtime >= cutoff:
                return True, f
    return False, "no artifact found"


def _next_selftask_archetype() -> str:
    now = int(time.time())
    return _SELFTASK_ARCHETYPES[now % len(_SELFTASK_ARCHETYPES)]


def _pick_bulk_model(archetype: str) -> Optional[str]:
    """Pick a bulk model for a self-task. Extraction needs a stronger model, so it
    avoids the tiny (0.8B/1.2B) models; compression/ideation are fine on tiny."""
    models = SELFTASK_BULK_MODELS
    if not models:
        return None
    if archetype == "corpus_extract":
        non_tiny = [m for m in models if "0.8b" not in m and "1.2b" not in m]
        if non_tiny:
            return random.choice(non_tiny)
    return random.choice(models)


# Structured compression/summarization template — mirrors Hermes's context-compaction
# prompt style (Summary / Decisions / Open Items / Artifacts) so small models produce
# DENSE, verifiable output instead of "Done - I've completed that for you."
COMPRESSION_PROMPT_TEMPLATE = """You are a summarization agent creating a compact, structured record.
Treat the input as source material. Produce ONLY the structured summary below — no greeting, no preamble.
Be CONCRETE: include file paths, commands, values, error messages, and decisions. Avoid vague phrases
like "made some changes" — say exactly what changed.
If there is nothing meaningful to summarize, write "No substantive content." — never just "done" or "completed".

## Summary
[2-4 sentence overview of what this is / was about]

## Key Points
- [concrete bullet: a fact, decision, or value]

## Decisions
- [decision and why it was made]

## Open Items
- [unresolved questions or next steps]

## Artifacts
- [files or paths referenced]
"""

# Files that already exist in the vault and must NOT count as a freshly produced artifact.
_KNOWLEDGE_TEMPLATE_FILES = {"Home.md", "README.md", "VAULT_INDEX.md", "ENV.md"}


def _build_structured_prompt(instruction: str, target_file: str, sections: List[str]) -> str:
    sec = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sections))
    return (
        f"BACKGROUND MAINTENANCE (low-priority, non-urgent). {instruction}\n"
        "Produce ONLY the structured content below as your response — no greeting, "
        "no preamble, and do NOT write any file yourself.\n"
        "Be CONCRETE: include file paths, commands, values, decisions. Avoid vague phrases.\n"
        "Do NOT write only 'done' / 'completed' — produce the actual content.\n\n"
        f"Use exactly these sections:\n{sec}\n"
    )


def _gather_knowledge_context(max_chars: int = 2500) -> str:
    """Bounded snapshot of the knowledge vault so small models can summarize
    without needing file/terminal tools (which they misuse and loop on)."""
    chunks: List[str] = []
    total = 0
    if not os.path.isdir(KNOWLEDGE_ROOT):
        return ""
    files: List[str] = []
    for name in os.listdir(KNOWLEDGE_ROOT):
        fp = os.path.join(KNOWLEDGE_ROOT, name)
        if os.path.isfile(fp) and name.endswith((".md", ".txt")) and name not in _KNOWLEDGE_TEMPLATE_FILES:
            files.append(fp)
        elif os.path.isdir(fp):
            for fn in os.listdir(fp):
                sub = os.path.join(fp, fn)
                if os.path.isfile(sub) and fn.endswith((".md", ".txt")):
                    files.append(sub)
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    for fp in files:
        if total >= max_chars:
            break
        try:
            with open(fp, "r", errors="ignore") as fh:
                txt = fh.read()
        except OSError:
            continue
        txt = txt[:1500]
        total += len(txt)
        chunks.append(f"# {os.path.relpath(fp, KNOWLEDGE_ROOT)}\n{txt}")
    return "\n\n".join(chunks)[:max_chars]


def _selftask_prompt(archetype: str, context: str = "") -> str:
    if archetype == "bulk_summarize":
        body = _build_structured_prompt(
            "Summarize the notes and documents under ~/knowledge into a single DENSE recap "
            "of what matters (decisions, open threads, facts).",
            "SUMMARY.md",
            ["Summary", "Key Points", "Decisions", "Open Items", "Artifacts"],
        )
    elif archetype == "session_compress":
        body = _build_structured_prompt(
            "Compress the most recent hermes session transcripts you can locate under ~/knowledge "
            "into a compact context recap that preserves decisions, tool outcomes, and unresolved items.",
            "recap.md",
            ["Active Task", "Completed Actions (numbered: action + file + outcome)",
             "In Progress", "Key Decisions", "Remaining Work"],
        )
    elif archetype == "corpus_extract":
        body = _build_structured_prompt(
            "Extract structured facts (entities, relationships, decisions) from documents under "
            "~/knowledge and append them as bullets.",
            "extracted_facts.md",
            ["Entities", "Relationships", "Decisions"],
        )
    elif archetype == "ideation":
        body = _build_structured_prompt(
            "Brainstorm concrete improvements and next ideas for the local swarm, grounded in the "
            "current ~/knowledge contents.",
            "IDEAS.md",
            ["Ideas (numbered, each with a one-line rationale)", "Top Pick", "Risks"],
        )
    else:  # triage (default)
        body = _build_structured_prompt(
            "Triage the current contents of ~/knowledge.",
            "TRIAGE.md",
            ["Stale", "High-Value", "Suggested Action"],
        )
    if context:
        return body + "\n\n---\nINPUT MATERIAL (already loaded; do NOT open files):\n" + context + "\n---\n"
    return body


def process_self_task(assistx: AssistXClient, archetype: Optional[str] = None) -> None:
    arch = archetype or _next_selftask_archetype()
    model = _pick_bulk_model(arch)
    if not model:
        logger.info("No SELFTASK_BULK_MODELS configured; skipping self-task %s", arch)
        return
    logger.info("Self-task %s on model %s", arch, model)
    context = _gather_knowledge_context()
    prompt = _selftask_prompt(arch, context)
    target_rel = _SELFTASK_TARGETS.get(arch, "SELFTASK.md")
    target_path = os.path.join(KNOWLEDGE_ROOT, target_rel)
    run_start = time.time()
    # Direct router call (no Hermes): small/tiny models are slow and Hermes sends
    # no max_tokens cap (unbounded generation -> timeout) and misuses file tools.
    # The adapter injects the vault context, caps output tokens, and persists the
    # returned text itself.
    result = call_self_task_llm(prompt, model, max_tokens=SELFTASK_MAX_TOKENS)
    # Small/tiny models are unreliable at writing files via tools, so the adapter
    # persists the returned text itself. Success = hermes ok AND non-trivial text
    # was produced AND we wrote it to the artifact path.
    output = (result.get("output") or "").strip()
    non_trivial = bool(output) and not is_trivial_output(output)
    artifact_present = False
    if non_trivial:
        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w") as fh:
                fh.write(output + "\n")
            artifact_present = True
        except OSError as exc:
            logger.error("Self-task %s failed to write %s: %s", arch, target_path, exc)
    actual_success = bool(result["success"]) and artifact_present
    record_task_eval(model, f"self:{arch}", actual_success, result.get("elapsed", 0), error=result.get("error"))
    logger.info("Self-task %s -> success=%s artifact=%s", arch, actual_success, artifact_present)


# ---------------------------------------------------------------------------
# Real task processing (routed + evaluated)
# ---------------------------------------------------------------------------
def process_task(assistx: AssistXClient, task: Dict[str, Any]) -> None:
    task_id = task.get("id")
    title = task.get("title", task.get("kind", f"Task {task_id}"))
    description = task.get("description", task.get("text", ""))

    session_id = uuid.uuid4().hex
    logger.info("Processing task %s: %s", task_id, title)

    if not assistx.claim_task(task_id, session_id):
        return

    context = assistx.get_context(task_id, title)
    context_refs = context.get("references", []) if isinstance(context, dict) else []
    context_text = ""
    if context_refs:
        snippets = []
        for ref in context_refs[:10]:
            snippet = ref.get("snippet", "")
            if snippet:
                snippets.append(f"- {snippet[:200]}")
        if snippets:
            context_text = "Relevant context:\n" + "\n".join(snippets)

    tier = classify_model_tier(task)
    model = select_tier_model(tier, seed=task_id)
    category = task_category(task)
    ensure_model_env(model)

    if tier == "compress-tiny":
        prompt = (
            f"Task: {title}\n\n"
            f"Description: {description}\n\n"
            f"{context_text}"
            f"{get_model_prompt(model)}\n\n"
            "This is a compression / summarization task. Produce a DENSE, structured record "
            "of the above using exactly these sections:\n"
            f"{COMPRESSION_PROMPT_TEMPLATE}"
        )
    else:
        prompt = (
            f"Task: {title}\n\n"
            f"Description: {description}\n\n"
            f"{context_text}"
            f"{get_model_prompt(model)}\n\n"
            "Please complete this task. Provide your response with:\n"
            "1. A brief summary of what you did\n"
            "2. Any important findings or decisions\n"
            "3. The final result or output"
        )

    assistx.heartbeat(task_id, session_id)

    start = time.monotonic()
    result = run_hermes(
        prompt,
        timeout=HERMES_TIMEOUT,
        model=model,
        provider=HERMES_PROVIDER,
        toolsets=HERMES_TOOLSETS,
    )
    elapsed = result.get("elapsed", time.monotonic() - start)

    hermes_session_id = result.get("session_id")
    if hermes_session_id:
        assistx.register_session(session_id, hermes_session_id)

    trivial = is_trivial_output(result.get("output", ""))
    actual_success = bool(result["success"]) and not trivial
    record_task_eval(model, category, actual_success, elapsed, error=result.get("error"), trivial=trivial)

    if result["success"]:
        output = result["output"]
        assistx.write_memory(
            kind="task_result",
            text=output[:2000],
            source=f"hermes:{AGENT_ID}:{model}",
            task_id=task_id,
            session_id=session_id,
        )
        assistx.complete_task(
            task_id=task_id,
            session_id=session_id,
            status="DONE",
            summary=output[:500],
            result={
                "output": output[:50000],
                "elapsed": elapsed,
                "model": model,
                "tier": tier,
                "hermes_session_id": hermes_session_id,
            },
        )
        logger.info("Task %s completed successfully on %s/%s", task_id, tier, model)
    else:
        error = result.get("error", "unknown")
        output = result.get("output", "")
        assistx.complete_task(
            task_id=task_id,
            session_id=session_id,
            status="FAILED",
            summary=f"Hermes error: {error}",
            result={
                "error": error,
                "output": output[:50000],
                "stderr": result.get("stderr", ""),
                "elapsed": elapsed,
                "model": model,
                "tier": tier,
            },
        )
        logger.info("Task %s failed on %s/%s: %s", task_id, tier, model, error)


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------
def run_loop(once: bool = False) -> None:
    logger.info(
        "Hermes agent adapter starting (agent=%s, capabilities=%s, poll_interval=%ds, bulk_models=%s)",
        AGENT_ID,
        AGENT_CAPABILITIES,
        POLL_INTERVAL,
        SELFTASK_BULK_MODELS,
    )

    assistx = AssistXClient()
    consecutive_empty = 0

    while True:
        try:
            tasks = assistx.poll_tasks(limit=MAX_TASKS_PER_LOOP)

            if not tasks:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    logger.info("No tasks available (poll %d)", consecutive_empty)
                if consecutive_empty >= SELFTASK_INTERVAL and SELFTASK_BULK_MODELS:
                    try:
                        process_self_task(assistx)
                    except Exception as e:
                        logger.exception("Self-task error: %s", e)
                    consecutive_empty = 0
            else:
                consecutive_empty = 0
                logger.info("Found %d available task(s)", len(tasks))

            for task in tasks:
                try:
                    process_task(assistx, task)
                except requests.HTTPError as e:
                    logger.error("API error on task %s: %s", task.get("id"), e)
                except Exception as e:
                    logger.exception("Unexpected error processing task %s: %s", task.get("id"), e)

            if once:
                logger.info("Single run complete")
                return

            time.sleep(POLL_INTERVAL)

        except requests.ConnectionError:
            logger.warning("Cannot connect to AssistX at %s (retry in %ds)", ASSISTX_URL, POLL_INTERVAL)
            if once:
                return
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Shutting down")
            return
        except Exception as e:
            logger.exception("Poll loop error: %s", e)
            if once:
                raise
            time.sleep(POLL_INTERVAL * 2)


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("HERMES_LOG_LEVEL", "INFO")),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    once = "--once" in sys.argv
    run_loop(once=once)


if __name__ == "__main__":
    main()
