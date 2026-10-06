from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_api_mounts_ed25519_projection_router() -> None:
    api_router = (ROOT / "src" / "assistx" / "api_router.py").read_text(
        encoding="utf-8"
    )
    projection = (
        ROOT / "src" / "assistx" / "runtime_projection_v2.py"
    ).read_text(encoding="utf-8")

    assert "from .runtime_projection_v2 import build_runtime_projection_router" in api_router
    assert "signature_algorithm" in projection
    assert '"Ed25519"' in projection
    assert "ASSISTX_RUNTIME_PROJECTION_SIGNING_KEY_FILE" in projection


def test_reconciliation_and_production_use_distinct_projection_private_key() -> None:
    for relative in (
        "compose.reconciliation.yml",
        "compose.production.reconciled.yml",
    ):
        content = (ROOT / relative).read_text(encoding="utf-8")
        assert "ASSISTX_RUNTIME_PROJECTION_SIGNING_KEY_FILE" in content
        assert "assistx_runtime_projection_private_key.pem" in content
        assert "ASSISTX_RUNTIME_PROJECTION_PRIVATE_KEY_FILE" in content
        assert "assistx_executor_private_key.pem" in content
        assert "ASSISTX_RUNTIME_PROJECTION_HMAC_SECRET" not in content


def test_executor_remains_profile_gated_and_strict() -> None:
    compose = (ROOT / "compose.reconciliation.yml").read_text(encoding="utf-8")

    assert "profiles:\n      - executor" in compose
    assert "python -m assistx.strict_executor_main" in compose
    assert "ASSISTX_STRICT_EXECUTOR_AUTH=true" in compose
    assert "HERMES_SELFTASK_ENABLED=false" in compose
    assert "FLEET_UNSAFE_SHELL_TASKS_ENABLED=false" in compose
    assert "cap_drop:\n      - ALL" in compose
    assert "no-new-privileges:true" in compose


def test_reconciliation_template_has_no_shared_projection_hmac() -> None:
    template = (ROOT / ".env.reconciliation.example").read_text(encoding="utf-8")

    assert "ASSISTX_RUNTIME_PROJECTION_PRIVATE_KEY_FILE" in template
    assert "ASSISTX_RUNTIME_PROJECTION_PUBLIC_KEY_FILE" in template
    assert "ASSISTX_EXECUTOR_PRIVATE_KEY_FILE" in template
    assert "ASSISTX_EXECUTOR_PUBLIC_KEY_FILE" in template
    assert "RUNTIME_PROJECTION_HMAC_SECRET" not in template
