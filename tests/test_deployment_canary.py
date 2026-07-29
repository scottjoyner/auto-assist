import os
import subprocess
import sys
from pathlib import Path

from assistx.deployment_canary import (
    CACHE_CHECKS,
    DeploymentCanary,
    load_environment_file,
    readiness_failures,
    validate_environment,
)


def valid_env():
    return {
        "BASIC_AUTH_USER": "operator",
        "BASIC_AUTH_PASS": "unique-password",
        "NEO4J_URI": "bolt://neo4j:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "unique-database-password",
        "NEO4J_DATABASE": "assistx_canary",
        "REDIS_URL": "redis://redis:6379/0",
        "OPENAI_BASE_URL": "http://inference:1234/v1",
        "LLM_MODEL": "model-a",
        "FLEET_UNSAFE_SHELL_TASKS_ENABLED": "false",
        "ASSISTX_RECOVERY_EXECUTION_ENABLED": "false",
    }


def test_observation_environment_requires_safe_mutation_defaults():
    env = valid_env()
    assert validate_environment(env, stages={"observe"}) == []

    env["FLEET_UNSAFE_SHELL_TASKS_ENABLED"] = "true"
    assert validate_environment(env, stages={"observe"}) == [
        "FLEET_UNSAFE_SHELL_TASKS_ENABLED must remain false"
    ]


def test_environment_file_preserves_json_values(tmp_path):
    path = tmp_path / "canary.env"
    path.write_text(
        '# comment\nASSISTX_FLEET_NODE_TOKENS={"xwing":"token"}\n'
        'QUOTED="value with spaces"\n'
    )

    assert load_environment_file(path) == {
        "ASSISTX_FLEET_NODE_TOKENS": '{"xwing":"token"}',
        "QUOTED": "value with spaces",
    }


def test_cache_environment_rejects_placeholders_and_missing_identity():
    env = valid_env()
    env.update(
        {
            "ASSISTX_KV_PREFIX_HMAC_SECRET": "replace-with-secret",
            "ASSISTX_FLEET_NODE_TOKENS": '{"node-a":"token"}',
            "CANARY_NODE_ID": "node-a",
            "CANARY_NODE_TOKEN": "token",
        }
    )

    errors = validate_environment(env, stages={"observe", "cache"})

    assert (
        "ASSISTX_KV_PREFIX_HMAC_SECRET still contains a placeholder" in errors
    )


def test_stage_readiness_does_not_require_recovery_during_observation():
    checks = [
        {
            "id": "control_execution",
            "ready": False,
            "detail": "disabled for observation",
        },
        {"id": "legacy_shell", "ready": True, "detail": "disabled"},
    ]

    assert readiness_failures({"checks": checks}, stages={"observe"}) == []


def test_cache_readiness_requires_identity_prefix_and_shell_gate():
    payload = {
        "checks": [
            {
                "id": check_id,
                "ready": check_id != "kv_prefix_identity",
                "detail": "test",
            }
            for check_id in CACHE_CHECKS
        ]
    }

    assert readiness_failures(payload, stages={"cache"}) == [
        "kv_prefix_identity: test"
    ]


class _TaskClient:
    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body, kwargs))
        if path == "/api/tasks":
            return 200, {"task_id": "task-canary"}
        if path.endswith("/claim"):
            return 200, {"claimed": True, "claim_id": "claim-canary"}
        if path.endswith("/heartbeat"):
            return 200, {"task": {"status": "RUNNING"}}
        if path.endswith("/complete") and body["claim_id"].startswith("stale-"):
            assert kwargs["expected"] == {404, 409}
            return 409, {"detail": "stale claim"}
        if path.endswith("/complete"):
            return 200, {"task": {"status": "DONE"}}
        raise AssertionError(f"unexpected call: {method} {path}")


def test_fenced_task_canary_proves_stale_completion_rejection():
    client = _TaskClient()
    canary = DeploymentCanary(
        client,
        node_id="node-a",
        node_token="token",
        stages={"observe"},
        run_id="run-a",
    )

    canary.run_fenced_task()

    evidence = canary.report["steps"]["fenced_task"]
    assert evidence["stale_completion_status"] == 409
    assert evidence["final_status"] == "DONE"


def test_improvement_canary_proposal_is_targeted(monkeypatch):
    from assistx import api

    class FakeNeo:
        values = None

        def upsert_ticket(self, **values):
            self.values = values
            return "task-improvement"

        def close(self):
            pass

    neo = FakeNeo()
    monkeypatch.setattr(api, "_neo", lambda: neo)
    body = api.ImprovementProposalIn(
        title="Deployment improvement canary",
        repository="auto-assist",
        objective="Make one bounded fixture-only canary update.",
        allowed_paths=["tests/fixtures/deployment_canary.txt"],
        verification_commands=[
            ["pytest", "-q", "tests/test_deployment_canary.py"]
        ],
        target_agent_id="assistx-canary",
    )

    result = api.api_create_improvement_proposal(body, "operator")

    assert result["status"] == "PROPOSED"
    assert neo.values["target_agent_id"] == "assistx-canary"


def test_initializer_generates_consistent_untracked_secrets(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source.env"
    source.write_text(
        "NEO4J_URI=bolt://neo4j:7687\n"
        "NEO4J_USER=neo4j\n"
        "NEO4J_PASSWORD=database-password\n"
        "NEO4J_DATABASE=assistx\n"
        "OPENAI_BASE_URL=http://inference:1234/v1\n"
        "OPENAI_API_KEY=not-needed\n"
        "LLM_MODEL=model-a\n"
        "EMBED_MODEL=embed-a\n"
    )
    output = tmp_path / "canary.env"
    node_output = tmp_path / "node.env"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/init-canary-env.py"),
            "--template",
            str(root / "deploy/canary.env.example"),
            "--source",
            str(source),
            "--output",
            str(output),
            "--node-output",
            str(node_output),
            "--recovery-node-id",
            "node-recovery",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )

    assert result.returncode == 0
    assert "secrets were generated but not printed" in result.stdout
    generated = load_environment_file(output)
    node = load_environment_file(node_output)
    registry = __import__("json").loads(
        generated["ASSISTX_FLEET_NODE_TOKENS"]
    )
    assert generated["NEO4J_PASSWORD"] == "database-password"
    assert registry["node-recovery"] == node["FLEET_NODE_TOKEN"]
    assert (
        generated["ASSISTX_RUNBOOK_SIGNING_KEYS"]
        == node["FLEET_RUNBOOK_VERIFY_KEYS"]
    )
    assert output.stat().st_mode & 0o777 == 0o600
    assert node_output.stat().st_mode & 0o777 == 0o600
    validation = subprocess.run(
        [
            sys.executable,
            "-m",
            "assistx.deployment_canary",
            "--env-file",
            str(output),
            "--validate-env",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    assert validation.returncode == 0, validation.stderr
