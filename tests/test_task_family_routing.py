from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.executor_security import ExecutorTokenCodec
from assistx.fleet_executor import FleetExecutor, ProjectionInventory
from assistx.task_family_routing import (
    infer_task_family,
    issue_executor_token,
    tag_task,
    virtual_model_for_family,
)


def _projection() -> ProjectionInventory:
    return ProjectionInventory(
        generation=7,
        revision="revision-7",
        expires_at_ms=9999999999999,
        providers=(),
        aliases=("small-summary", "large-code"),
        total_slots=2,
    )


def test_repo_and_harvester_tasks_receive_explicit_families() -> None:
    repo = {
        "kind": "repo_analysis",
        "title": "Review service.py",
        "payload": {"prompt": "Review this Python service."},
    }
    memory = {
        "kind": "kg_insight",
        "title": "Memory Synthesis [decision]",
        "payload": {"prompt": "Synthesize related memories."},
    }
    paper = {
        "kind": "kg_insight",
        "title": "Paper: Routing Smaller Models",
        "payload": {"prompt": "Analyze this research paper."},
    }

    assert tag_task(repo)["payload"]["task_family"] == "coding"
    assert repo["payload"]["model"] == "auto/code"
    assert tag_task(memory)["payload"]["task_family"] == "summarization"
    assert memory["payload"]["model"] == "auto/summarize"
    assert tag_task(paper)["payload"]["task_family"] == "reasoning"
    assert paper["payload"]["model"] == "auto/high-quality"


def test_executor_preserves_virtual_alias_for_gateway_selection() -> None:
    executor = FleetExecutor.__new__(FleetExecutor)
    task = {
        "id": "task-compress-1",
        "priority": "background",
        "kind": "context_compaction",
        "payload": {
            "task_family": "compression",
            "prompt": "Compress this context.",
        },
    }

    request = executor._request_payload(task, "claim-1", _projection())

    assert request["model"] == "auto/compress"
    assert request["metadata"]["task_family"] == "compression"
    assert request["metadata"]["queue_class"] == "background"
    assert request["metadata"]["assistx_executor"] == {
        "task_id": "task-compress-1",
        "claim_id": "claim-1",
        "agent_id": "fleet-executor",
        "projection_generation": 7,
    }


def test_virtual_alias_token_is_task_and_claim_scoped(monkeypatch) -> None:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    monkeypatch.setenv("ASSISTX_EXECUTOR_SIGNING_KEY_PEM", private_pem)
    monkeypatch.setenv("ASSISTX_EXECUTOR_KEY_ID", "routing-test-key")
    request = {
        "model": "auto/compress",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "compress"}],
        "metadata": {
            "task_family": "compression",
            "assistx_executor": {
                "task_id": "task-compress-1",
                "claim_id": "claim-1",
                "agent_id": "fleet-executor",
                "projection_generation": 7,
            },
        },
    }

    token = issue_executor_token(request)
    claims = ExecutorTokenCodec(
        public_key=private.public_key(),
        key_id="routing-test-key",
    ).decode(token, audience="auto-router")

    assert claims["task_id"] == "task-compress-1"
    assert claims["claim_id"] == "claim-1"
    assert claims["projection_generation"] == 7
    assert claims["allowed_model_aliases"] == ["auto/compress"]
    assert claims["scopes"] == ["inference"]


def test_explicit_concrete_model_is_still_projection_validated() -> None:
    executor = FleetExecutor.__new__(FleetExecutor)
    task = {
        "id": "task-explicit-1",
        "kind": "kg_insight",
        "payload": {
            "model": "large-code",
            "task_family": "reasoning",
            "prompt": "Analyze this incident.",
        },
    }

    request = executor._request_payload(task, "claim-2", _projection())

    assert request["model"] == "large-code"
    assert request["metadata"]["task_family"] == "reasoning"


def test_family_aliases_are_stable() -> None:
    assert infer_task_family(payload={"task_family": "summary"}) == "summarization"
    assert virtual_model_for_family("compression") == "auto/compress"
    assert virtual_model_for_family("unknown") == ""
