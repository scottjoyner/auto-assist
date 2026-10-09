from __future__ import annotations

import os
import re
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


def test_deploy_helper_requires_exact_source_and_caddy_fence() -> None:
    text = (ROOT / "scripts" / "deploy-kipnerter-tailnet-gateway.sh").read_text(
        encoding="utf-8"
    )
    assert "KIPNERTER_GATEWAY_SOURCE_SHA" in text
    assert "does not match required backend SHA" in text
    assert "working tree is dirty" in text
    assert "KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED" in text
    assert "legacy x1-370 Caddy Tailscale-header fence is not confirmed" in text
    assert "scottjoyner/Sophia#13" in text
    assert 'KIPNERTER_GATEWAY_SERVE_PORT:-8443' in text


def _mobile_paths_from(text: str) -> list[str]:
    match = re.search(r"MOBILE_PATHS=\((.*?)\n\s*\)", text, re.DOTALL)
    assert match, "MOBILE_PATHS is no longer declared; the mobile surface lost its source of truth"
    return [line.strip() for line in match.group(1).strip().splitlines() if line.strip()]


def test_serve_helper_exposes_only_mobile_surface_and_preserves_other_roots() -> None:
    text = (ROOT / "scripts" / "configure-kipnerter-tailnet-serve.sh").read_text(
        encoding="utf-8"
    )
    assert 'KIPNERTER_GATEWAY_SERVE_PORT:-8443' in text
    assert '--https="${SERVE_PORT}"' in text
    paths = _mobile_paths_from(text)
    assert "/health" in paths
    assert "/api/v1/runtime/catalog" in paths
    assert "/api/v1/agent/chat/completions" in paths
    # Per-model selection is only reachable if the edge publishes it. This route
    # was served by FastAPI and tested while the phone could not reach it.
    assert "/api/v1/model/chat/completions" in paths
    assert 'serve --https=443 --set-path' not in text
    assert "Existing unrelated Serve roots and Funnel mappings were not reset or replaced" in text
    assert "Serve path ${path} is already owned by another target" in text
    assert "tailscale serve reset" not in text


def test_every_mobile_path_is_probed_and_mounted_from_the_same_list() -> None:
    """The edge must publish exactly what the verifier checks.

    These were two independent hardcoded lists. A route added to one and not the
    other failed silently: FastAPI served it, tests passed, and the phone could not
    reach it. Asserting they agree is what makes adding a route safe next time.
    """
    configure = (ROOT / "scripts" / "configure-kipnerter-tailnet-serve.sh").read_text(
        encoding="utf-8"
    )
    verify = (ROOT / "scripts" / "verify-kipnerter-tailnet-gateway.sh").read_text(
        encoding="utf-8"
    )
    declared = _mobile_paths_from(configure)
    probed_block = re.search(r"(/health \\\n(?:.*\\\n)*?\s*/api/v1/[^;]+); do", verify)
    assert probed_block, "verifier no longer enumerates the mobile paths"
    probed = [
        token
        for token in probed_block.group(1).replace("\\", "").split()
        if token.startswith("/")
    ]
    assert declared == probed, f"mounted {declared} but probed {probed}"
    # Each mounted path must be proxied to the same path on the backend.
    assert '"--set-path=${path}"' in configure
    assert '"http://127.0.0.1:${API_PORT}${path}"' in configure
    assert '"--set-path=${path}"' not in verify


def test_verifier_proves_scope_identity_and_hermes_marker() -> None:
    text = (ROOT / "scripts" / "verify-kipnerter-tailnet-gateway.sh").read_text(
        encoding="utf-8"
    )
    assert 'KIPNERTER_GATEWAY_SERVE_PORT:-8443' in text
    assert 'gateway_url="https://${dns_name}:${SERVE_PORT}"' in text
    assert "/api/v1/auth/whoami" in text
    assert "/api/v1/runtime/catalog" in text
    assert 'value.get("schema_version") != "2"' in text
    assert "agent_auto_available" in text
    assert "/api/v1/agent/chat/completions" in text
    assert "/api/degraded/status" in text
    assert "X-Kipnerter-Agent-Executor" in text
    assert "whoami provider is not tailscale" in text
    assert "serve_scope=mobile-paths-only" in text
    assert "unrelated root mount" in text


def test_gateway_deploy_preflight_never_mutates_without_physical_authority():
    """No caller-provided source pin should cause unattended Serve changes."""
    script = ROOT / "scripts" / "deploy-kipnerter-tailnet-gateway.sh"
    text = script.read_text(encoding="utf-8")
    assert "sudo tailscale serve" not in text
    assert "tailscale serve reset" not in text
    assert "docker compose up" not in text
    assert 'fail "all declarative preflights satisfied but NO deployment authority' in text
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp")}
    result = subprocess.run(
        ["bash", str(script)], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False, timeout=5,
    )
    assert result.returncode == 78
    assert "HOLD" in result.stderr
    assert "KIPNERTER_GATEWAY_SOURCE_SHA" in result.stderr
