from __future__ import annotations

import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_executor_containment",
    ROOT / "scripts" / "validate-executor-containment.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def safe_payload(home: pathlib.Path, artifacts: pathlib.Path) -> dict:
    return {
        "services": {
            "hermes-adapter": {
                "profiles": ["executor"],
                "restart": "no",
                "user": "1000:1000",
                "read_only": True,
                "networks": ["default"],
                "extra_hosts": [],
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "environment": {
                    "AUTO_ASSIGN_BASE_URL": "",
                    "HERMES_SELFTASK_ENABLED": "false",
                    "FLEET_UNSAFE_SHELL_TASKS_ENABLED": "false",
                    "ASSISTX_TOOL_EGRESS_MODE": "disabled",
                    "HERMES_AGENT_CAPABILITIES": "terminal,file,code_execution,skills,memory,todo",
                    "HERMES_TOOLSETS": "terminal,file,code_execution,skills,memory,todo",
                    "OPENROUTER_API_KEY": "",
                },
                "volumes": [
                    {"type": "bind", "source": str(home), "target": "/app/hermes-home"},
                    {"type": "bind", "source": str(artifacts), "target": "/app/artifacts"},
                ],
            }
        }
    }


def test_containment_accepts_scoped_non_root_executor(tmp_path, monkeypatch) -> None:
    home = tmp_path / "hermes-home"
    artifacts = tmp_path / "artifacts"
    home.mkdir()
    artifacts.mkdir()
    monkeypatch.setenv("RECONCILIATION_HERMES_HOME", str(home))

    assert module.validate(safe_payload(home, artifacts)) == []


def test_containment_rejects_broad_mount_root_and_web_tools(tmp_path, monkeypatch) -> None:
    home = tmp_path / "hermes-home"
    artifacts = tmp_path / "artifacts"
    home.mkdir()
    artifacts.mkdir()
    monkeypatch.setenv("RECONCILIATION_HERMES_HOME", str(home))
    payload = safe_payload(home, artifacts)
    service = payload["services"]["hermes-adapter"]
    service["user"] = "0:1000"
    service["environment"]["HERMES_TOOLSETS"] += ",web,browser,mcp"
    service["volumes"].append(
        {"type": "bind", "source": "/home/scott/git", "target": "/workspace"}
    )

    failures = module.validate(payload)

    assert any("non-root" in item for item in failures)
    assert any("forbidden capability" in item for item in failures)
    assert any("broad or sensitive" in item or "unapproved bind" in item for item in failures)
