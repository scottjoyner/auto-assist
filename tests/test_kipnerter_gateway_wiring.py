from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_gateway_shell_scripts_parse() -> None:
    scripts = [
        ROOT / "scripts" / "configure-kipnerter-tailnet-serve.sh",
        ROOT / "scripts" / "deploy-kipnerter-tailnet-gateway.sh",
        ROOT / "scripts" / "verify-kipnerter-tailnet-gateway.sh",
    ]
    subprocess.run(["bash", "-n", *map(str, scripts)], check=True)


def test_gateway_env_overlay_is_fail_closed() -> None:
    text = (ROOT / ".env.kipnerter-gateway.example").read_text(encoding="utf-8")
    assert "ASSISTX_API_BIND=127.0.0.1" in text
    assert "TRUSTED_AUTH_HEADER=Tailscale-User-Login" in text
    assert "KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE=0" in text
    assert "KIPNERTER_TAILNET_ALLOWED_LOGINS=" in text


def test_deploy_helper_requires_exact_source_when_requested() -> None:
    text = (ROOT / "scripts" / "deploy-kipnerter-tailnet-gateway.sh").read_text(
        encoding="utf-8"
    )
    assert "KIPNERTER_GATEWAY_SOURCE_SHA" in text
    assert "does not match required backend SHA" in text
    assert "working tree is dirty" in text


def test_verifier_proves_identity_and_hermes_marker() -> None:
    text = (ROOT / "scripts" / "verify-kipnerter-tailnet-gateway.sh").read_text(
        encoding="utf-8"
    )
    assert "/api/v1/auth/whoami" in text
    assert "/api/v1/agent/chat/completions" in text
    assert "X-Kipnerter-Agent-Executor" in text
    assert "whoami provider is not tailscale" in text
