"""Protect the separate ingress-auth and Hermes-mount issue references."""
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
INGRESS_DOCUMENTS=(
    "ASSISTX_MOBILE_SERVE_SCOPE_DEPENDENCY_HOLD_20261023.md",
    "ASSISTX_TAILNET_DUPLICATE_IDENTITY_HEADER_CANARY_20261023.md",
    "ASSISTX_TAILNET_INGRESS_BOOT_FENCE_HOLD_20261023.md",
    "ASSISTX_TAILNET_POSTCUTOVER_ATTESTATION_HOLD_20261023.md",
    "ASSISTX_TAILNET_SANITIZER_CUTOVER_HOLD_20261022.md",
    "ASSISTX_SANITIZER_FAULT_ROLLBACK_HOLD_20261023.md",
)


@pytest.mark.parametrize("document",INGRESS_DOCUMENTS)
def test_ingress_docs_target_trusted_header_issue_149(document):
    text=(ROOT/"docs"/document).read_text(encoding="utf-8")
    assert "ingress P0" in text.splitlines()[1:5].__str__()
    assert "#149" in text.splitlines()[1:5].__str__()
    assert "P0 #414 |" not in text
    assert "P0 issue #414 |" not in text


def test_hermes_mount_issue_414_remains_separate():
    main=(ROOT/"docs/ASSISTX_TAILNET_SANITIZER_CUTOVER_HOLD_20261022.md").read_text()
    assert "Production Hermes still has 16 legacy mounts" in main
    assert "issue #414 open" in main
    other=(ROOT/"docs/ASSISTX_LEGACY_HEADER_EXACT_IMAGE_CANARY_HOLD_20261022.md").read_text()
    assert "ingress P0 issue #149" in other
    assert "separate Hermes writable-mount P0 issue #414" in other
