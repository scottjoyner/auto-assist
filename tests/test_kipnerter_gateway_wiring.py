from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_gateway_shell_scripts_parse() -> None:
    scripts = [
        ROOT / "scripts" / "configure-kipnerter-tailnet-serve.sh",
        ROOT / "scripts" / "verify-kipnerter-tailnet-gateway.sh",
    ]
    subprocess.run(["bash", "-n", *map(str, scripts)], check=True)


def _deferred_release_decision() -> str:
    path = ROOT / "docs/releases/KIPNERTER_TAILNET_GATEWAY_DEFERRED_20261009.md"
    assert path.is_file(), "missing reviewed explicit gateway release disposition"
    decision = path.read_text(encoding="utf-8")
    assert "gateway_deferred=true" in decision
    assert "production_deployment_authorized=false" in decision
    assert "automatic_environment_mutation_authorized=false" in decision
    assert "AssistX trusted-header issue #149" in decision
    return decision


def test_retired_gateway_env_overlay_is_not_implicitly_restored() -> None:
    _deferred_release_decision()
    # A value-bearing legacy env template must not reappear via CI fixes.
    assert not (ROOT / ".env.kipnerter-gateway.example").exists()


def test_retired_gateway_deploy_helper_does_not_bypass_ingress_acceptance() -> None:
    _deferred_release_decision()
    # The historical helper recreated the API and rewrote .env before
    # verifying proxy identity custody. Reintroducing it is a new review.
    assert not (ROOT / "scripts/deploy-kipnerter-tailnet-gateway.sh").exists()



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
