"""Harness evolution chain: reflect -> train -> deploy -> rescore, as fenced
AssistX tasks with a deterministic, idempotent chain identity.

Pure chain layer (no I/O). Runtime ownership stays where it belongs —
auto-finetune runs training, node agents run deployments, the benchmark CLI
runs rescores; this module sequences and evidences them. Stage outcomes are
recorded through ``neo4j_client.create_evaluation_run`` with the metadata dict
from :func:`evidence_metadata`, forming the EvaluationRun lineage that the
promotion gates (W5) will consume.

Chain identity is derived from ``(suite_id, endpoint, base_run_identity)`` so
retrying or rebuilding any stage yields the same idempotency keys — the chain
is a fact about the benchmark run, not about when it was planned.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import harness_reflect as hr
from .evaluation_registry import HarnessSuiteDef

CHAIN_STAGES = ("reflect", "train", "deploy", "rescore")

DEPLOY_METHODS = ("lora_adapters_swap", "merged_gguf_repoint")


@dataclass(frozen=True)
class HarnessChain:
    suite_id: str
    endpoint: str
    model_key: str
    base_run_identity: str

    @property
    def chain_id(self) -> str:
        digest = hashlib.sha256(
            f"{self.suite_id}|{self.endpoint}|{self.base_run_identity}".encode()
        ).hexdigest()[:16]
        return f"hchain-{digest}"

    @property
    def target_node(self) -> str:
        """Node part of the endpoint (``optiplex:1235`` -> ``optiplex``)."""
        return self.endpoint.split(":", 1)[0]

    def lineage(self, stage: str) -> dict[str, Any]:
        if stage not in CHAIN_STAGES:
            raise ValueError(f"unknown chain stage: {stage}")
        return {
            "chain_id": self.chain_id,
            "stage": stage,
            "suite_id": self.suite_id,
            "endpoint": self.endpoint,
            "model_key": self.model_key,
            "base_run_identity": self.base_run_identity,
        }


def chain_from_run(run: dict[str, Any]) -> HarnessChain:
    """Chain identity for a benchmark run artifact (see harness_reflect)."""
    suite_id = str(run.get("suite_id") or "").strip()
    endpoint = str(run.get("endpoint") or "").strip()
    model_key = str(run.get("model_key") or "").strip()
    if not suite_id or not endpoint or not model_key:
        raise ValueError(
            "run artifact must carry suite_id, endpoint, and model_key"
        )
    return HarnessChain(
        suite_id=suite_id,
        endpoint=endpoint,
        model_key=model_key,
        base_run_identity=hr.run_identity(run),
    )


def _stamped(task: dict[str, Any], chain: HarnessChain, stage: str) -> dict[str, Any]:
    task["payload"]["chain"] = chain.lineage(stage)
    return task


def reflect_task(
    chain: HarnessChain,
    run: dict[str, Any],
    *,
    target_agent_id: str | None = None,
) -> dict[str, Any]:
    """Reflect stage (PR #44 builder), stamped into the chain."""
    task = hr.build_reflect_task(
        run,
        target_agent_id=target_agent_id or chain.target_node,
    )
    return _stamped(task, chain, "reflect")


def train_task(
    chain: HarnessChain,
    *,
    dataset_ref: str,
    gpu_node: str,
    train_config: str,
    output_dir: str,
    ttl_seconds: int = 4 * 3600,
) -> dict[str, Any]:
    """Train stage: lease a GPU and run auto-finetune on the correction pairs.

    ``train_config``/``output_dir`` reference the auto-finetune repo (e.g. the
    iter3/4/5 TRL-SFT configs); AssistX does not own the training code."""
    if not dataset_ref:
        raise ValueError("dataset_ref is required (reflect-stage output)")
    if not gpu_node or not train_config or not output_dir:
        raise ValueError("gpu_node, train_config, and output_dir are required")
    task = {
        "title": f"Harness train {chain.suite_id} for {chain.endpoint}",
        "kind": "harness_train",
        "required_capabilities": ["finetune"],
        "target_agent_id": gpu_node,
        "priority": "BATCH",
        "preemptible": False,
        "max_migrations": 0,
        "idempotency_key": f"harness-train:{chain.chain_id}",
        "payload": {
            "queue_class": "batch",
            "harness_train": True,
            "dataset_ref": dataset_ref,
            "train_config": train_config,
            "output_dir": output_dir,
            # fleet_reserve keys are node:port; a training lease reserves the
            # node's GPU as a whole.
            "reservation_request": {
                "node": gpu_node,
                "resource": "gpu",
                "ttl_seconds": ttl_seconds,
            },
            "deadline_seconds": 6 * 3600,
            "allow_model_load": False,
        },
    }
    return _stamped(task, chain, "train")


def deploy_task(
    chain: HarnessChain,
    *,
    artifact_ref: str,
    artifact_sha256: str,
    deploy_method: str,
) -> dict[str, Any]:
    """Deploy stage: put the trained artifact on the chain's endpoint.

    Deployments are operator-in-loop (the task is created for review; the node
    agent executes only an approved method) and gate traffic behind runtime
    admission re-approval of the endpoint's AccessPath projection."""
    if deploy_method not in DEPLOY_METHODS:
        raise ValueError(
            f"deploy_method must be one of {DEPLOY_METHODS}, got {deploy_method!r}"
        )
    if not artifact_ref or not artifact_sha256:
        raise ValueError("artifact_ref and artifact_sha256 are required")
    task = {
        "title": f"Harness deploy {chain.model_key} to {chain.endpoint}",
        "kind": "harness_deploy",
        "required_capabilities": ["deploy"],
        "target_agent_id": chain.target_node,
        "priority": "HIGH",
        "preemptible": False,
        "max_migrations": 0,
        "idempotency_key": f"harness-deploy:{chain.chain_id}",
        "payload": {
            "queue_class": "batch",
            "harness_deploy": True,
            "endpoint": chain.endpoint,
            "model_key": chain.model_key,
            "artifact": {
                "ref": artifact_ref,
                "sha256": artifact_sha256,
                "model_key": chain.model_key,
            },
            "deploy_method": deploy_method,
            "requires_runtime_admission": True,
            "deadline_seconds": 1800,
        },
    }
    return _stamped(task, chain, "deploy")


def rescore_task(
    chain: HarnessChain,
    suite: HarnessSuiteDef,
    *,
    attempt: int = 1,
) -> dict[str, Any]:
    """Rescore stage: re-run the SAME suite on the SAME endpoint, at least
    ``suite.min_trials`` times (the registry's trial rule is machine-carried)."""
    task = {
        "title": f"Harness rescore {suite.id} on {chain.endpoint} (attempt {attempt})",
        "kind": "harness_rescore",
        "required_capabilities": ["llm"],
        "target_agent_id": chain.target_node,
        "priority": "BATCH",
        "preemptible": True,
        "max_migrations": 2,
        "idempotency_key": f"harness-rescore:{chain.chain_id}:{attempt}",
        "payload": {
            "queue_class": "batch",
            "harness_rescore": True,
            "suite_id": suite.id,
            "harness_id": suite.id,
            "schema_version": suite.schema_version,
            "endpoint": chain.endpoint,
            "model_key": chain.model_key,
            "trials": suite.min_trials,
            "attempt": attempt,
            "compare_to_base_run": chain.base_run_identity,
            "deadline_seconds": 2 * 3600,
        },
    }
    return _stamped(task, chain, "rescore")


def execute_reflect_task(
    task: dict[str, Any],
    llm_call: Callable[[str], str],
) -> dict[str, Any]:
    """Executor glue for the reflect stage.

    ``llm_call`` is the worker's injected endpoint call (prompt -> completion
    text). The reflection is extracted and validated against the task's fail
    set before anything downstream may consume it."""
    payload = task.get("payload") or {}
    if not payload.get("harness_reflect"):
        raise ValueError("task is not a harness_reflect task")
    prompt = str(payload.get("prompt") or "")
    raw = llm_call(prompt)
    reflection = hr.extract_reflection(raw)
    errors: list[str] = []
    uncovered: list[str] = []
    if reflection is None:
        errors.append("no JSON object found in model output")
    else:
        errors, uncovered = hr.validate_reflection(
            reflection, payload.get("fail_set") or []
        )
    return {
        "ok": reflection is not None and not errors,
        "reflection": reflection,
        "errors": errors,
        "uncovered_fails": uncovered,
        "evidence": {
            **(payload.get("chain") or {}),
            "run_identity": payload.get("run_identity"),
            "suite_id": payload.get("suite_id"),
            "endpoint": payload.get("endpoint"),
        },
    }


def evidence_metadata(
    chain: HarnessChain,
    stage: str,
    *,
    status: str,
    score: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Metadata dict for ``neo4j_client.create_evaluation_run`` — the stage
    outcome's place in the EvaluationRun lineage."""
    metadata = {
        **chain.lineage(stage),
        "harness_evolution": True,
        "status": status,
    }
    if score is not None:
        metadata["score"] = score
    metadata.update(extra)
    return metadata
