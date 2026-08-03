from __future__ import annotations

import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_hermes_external_config",
    ROOT / "scripts" / "validate-hermes-external-config.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def safe_config() -> dict:
    return {
        "model": {
            "provider": "custom",
            "default": "auto/code",
            "base_url": "http://auto-router-reconciliation:8088/v1",
        },
        "fleet": {
            "mode": "external",
            "external": {
                "base_url": "http://auto-router-reconciliation:8088/v1",
                "admin_url": "http://auto-router-reconciliation:8088",
                "admin_token_env": "AUTO_ROUTER_ADMIN_TOKEN",
                "default_model": "auto/code",
                "strict_offline": True,
            },
        },
        "plugins": {"enabled": ["fleet-router", "small-model-context"]},
        "max_concurrent_sessions": 1,
    }


def test_external_config_accepts_single_gateway_authority():
    assert module.validate(safe_config()) == []


def test_external_config_rejects_nodes_public_gateway_and_static_model():
    payload = safe_config()
    payload["fleet"]["nodes"] = [
        {"name": "xwing", "base_url": "http://xwing.lan:1234/v1"}
    ]
    payload["fleet"]["external"]["base_url"] = "https://api.example.com/v1"
    payload["model"]["base_url"] = "https://api.example.com/v1"
    payload["fleet"]["external"]["default_model"] = "qwen.gguf"
    payload["model"]["default"] = "qwen.gguf"

    failures = module.validate(payload)

    assert any("fleet.nodes is forbidden" in item for item in failures)
    assert any("private http(s) URL" in item for item in failures)
    assert any("auto/*" in item for item in failures)
