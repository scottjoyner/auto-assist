from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_baked_hermes_router_uses_existing_scoped_inference_token_env() -> None:
    config = (ROOT / "hermes_config.yaml").read_text()
    provider_block = config.split("  assistx-router:\n", 1)[1]

    assert "key_env: FLEET_ROUTER_BEARER_TOKEN" in provider_block
    assert "api_key:" not in provider_block


def test_api_service_already_receives_scoped_router_inference_token() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()

    assert (
        "FLEET_ROUTER_BEARER_TOKEN=${AUTO_ROUTER_INTERNAL_SERVICE_TOKEN:"
        "?set AssistX-only router inference token}"
    ) in compose
