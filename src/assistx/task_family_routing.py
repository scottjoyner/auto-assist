from __future__ import annotations

import copy
from typing import Any

_VIRTUAL_MODELS = {
    "coding": "auto/code",
    "reasoning": "auto/high-quality",
    "tool_use": "auto/high-quality",
    "long_context": "auto/review",
    "summarization": "auto/summarize",
    "compression": "auto/compress",
    "extraction": "auto/extract",
}

_ALIASES = {
    "code": "coding",
    "code_review": "coding",
    "research": "reasoning",
    "analysis": "reasoning",
    "tools": "tool_use",
    "tool": "tool_use",
    "summary": "summarization",
    "summarize": "summarization",
    "compress": "compression",
    "extract": "extraction",
    "context": "long_context",
}

_INSTALLED = False


def infer_task_family(
    task: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    task = task or {}
    payload = payload or {}
    explicit = str(
        payload.get("task_family")
        or payload.get("workload_class")
        or task.get("task_family")
        or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")
    if explicit:
        return _ALIASES.get(explicit, explicit)

    kind = str(task.get("kind") or payload.get("kind") or "").lower()
    title = str(task.get("title") or payload.get("title") or "").lower()
    prompt = str(payload.get("prompt") or "").lower()
    text = f"{kind} {title} {prompt}"
    if kind.startswith("repo_") or "repository" in text and "code" in text:
        return "coding"
    if any(token in text for token in ("compress", "compaction", "compact context")):
        return "compression"
    if any(token in text for token in ("extract", "structured fields", "entity extraction")):
        return "extraction"
    if any(token in text for token in ("summar", "synthesis", "throughput report")):
        return "summarization"
    if any(token in text for token in ("tool use", "tool-use", "function calling")):
        return "tool_use"
    if any(token in text for token in ("long context", "large context", "context review")):
        return "long_context"
    if kind == "kg_insight" or any(
        token in text
        for token in ("paper:", "signal analysis", "failure analysis", "research")
    ):
        return "reasoning"
    return "general"


def virtual_model_for_family(family: str) -> str:
    return _VIRTUAL_MODELS.get(str(family or "").strip().lower(), "")


def tag_task(task: dict[str, Any], family: str | None = None) -> dict[str, Any]:
    payload = task.get("payload")
    if not isinstance(payload, dict):
        return task
    selected = family or infer_task_family(task, payload)
    if selected == "general":
        return task
    payload["task_family"] = selected
    payload.setdefault("workload_class", selected)
    if not str(payload.get("model") or "").strip():
        payload["model"] = virtual_model_for_family(selected)
    return task


def _install_harvester_policy() -> None:
    from . import kg_harvester

    original = kg_harvester.KgInsightHarvester._create_llm_task
    if getattr(original, "_task_family_routing", False):
        return

    def create_llm_task(
        self: Any,
        title: str,
        messages: list[dict],
        model_hint: str = "",
        idempotency_key: str = "",
    ) -> None:
        before = len(self._batch)
        original(
            self,
            title,
            messages,
            model_hint=model_hint,
            idempotency_key=idempotency_key,
        )
        for task in self._batch[before:]:
            tag_task(task)

    create_llm_task._task_family_routing = True  # type: ignore[attr-defined]
    kg_harvester.KgInsightHarvester._create_llm_task = create_llm_task


def _install_repository_policy() -> None:
    from . import repo_task_generator

    original_analysis = repo_task_generator._analysis_task
    original_proposal = repo_task_generator._mutation_proposal
    if getattr(original_analysis, "_task_family_routing", False):
        return

    def analysis_task(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        task = original_analysis(*args, **kwargs)
        return tag_task(task, "coding") if task else None

    def mutation_proposal(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        task = original_proposal(*args, **kwargs)
        return tag_task(task, "coding") if task else None

    analysis_task._task_family_routing = True  # type: ignore[attr-defined]
    mutation_proposal._task_family_routing = True  # type: ignore[attr-defined]
    repo_task_generator._analysis_task = analysis_task
    repo_task_generator._mutation_proposal = mutation_proposal


def _install_executor_policy() -> None:
    from . import fleet_executor

    original = fleet_executor.FleetExecutor._request_payload
    if getattr(original, "_task_family_routing", False):
        return

    def request_payload(
        self: Any,
        task: dict[str, Any],
        claim_id: str,
        projection: Any,
    ) -> dict[str, Any]:
        payload = self._payload(task)
        messages = self._messages(payload)
        if not messages:
            raise ValueError("task does not contain messages or a prompt")
        family = infer_task_family(task, payload)
        complexity = str(
            payload.get("complexity") or payload.get("quality") or ""
        ).strip().lower()
        requested = str(payload.get("model") or "").strip()
        virtual = virtual_model_for_family(family)
        if requested.startswith("auto/"):
            model = requested
        elif requested:
            model = projection.choose_model(requested, complexity)
        elif virtual:
            model = virtual
        else:
            model = projection.choose_model("", complexity)

        metadata = copy.deepcopy(payload.get("metadata") or {})
        if not isinstance(metadata, dict):
            metadata = {}
        if family != "general":
            metadata["task_family"] = family
            metadata["workload_class"] = family
        metadata["queue_class"] = str(
            payload.get("queue_class") or task.get("priority") or "interactive"
        ).lower()
        metadata["assistx_executor"] = {
            "task_id": str(task.get("id") or ""),
            "claim_id": claim_id,
            "agent_id": fleet_executor.EXECUTOR_AGENT_ID,
            "projection_generation": projection.generation,
        }
        return {
            "model": model,
            "messages": messages,
            "temperature": float(payload.get("temperature", 0.2)),
            "max_tokens": max(
                1,
                min(int(payload.get("max_tokens", 4096)), 32768),
            ),
            "stream": False,
            "metadata": metadata,
        }

    request_payload._task_family_routing = True  # type: ignore[attr-defined]
    fleet_executor.FleetExecutor._request_payload = request_payload


def install_task_family_routing() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_harvester_policy()
    _install_repository_policy()
    _install_executor_policy()
    _INSTALLED = True
