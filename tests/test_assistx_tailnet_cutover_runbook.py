"""Release runbook must never prescribe an unsafe socat rollback."""
from __future__ import annotations

import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MAIN=ROOT/"docs/ASSISTX_TAILNET_SANITIZER_CUTOVER_HOLD_20261022.md"
BOOT=ROOT/"docs/ASSISTX_TAILNET_INGRESS_BOOT_FENCE_HOLD_20261023.md"
MOBILE=ROOT/"docs/ASSISTX_MOBILE_SERVE_SCOPE_DEPENDENCY_HOLD_20261023.md"


def handoff():
    text=MAIN.read_text(encoding="utf-8")
    start=text.index("## Bounded cutover / rollback handoff")
    end=text.index("## Other P0 gates",start)
    return text[start:end]


def test_handoff_explicitly_rejects_raw_socat_recovery():
    text=handoff()
    assert "not as an executable rollback" in text
    assert "sanitizing Caddy fallback" in text
    assert "fail-closed/offline" in text
    assert "not the old socat forwarder" in text
    assert "never return to raw socat" in text


def test_handoff_does_not_prescribe_restoring_insecure_service():
    text=handoff().lower()
    for old in (
        "restore/start the exact old service",
        "rollback copy of the original",
        "restore the original socat",
        "restart the old forwarder",
        "re-enable the original service",
    ):
        assert old not in text


def test_boot_persistence_is_mandatory_during_handover():
    text=handoff()
    assert "legacy socat unit must be inactive and disabled" in text
    assert "stays inactive/disabled" in text
    assert "exclusive port ownership" in text
    assert "exact active Caddy config" in text


def test_canary_is_not_incorrectly_treated_as_cutover_proof():
    text=handoff()
    assert "Do not claim success from synthetic tests alone" in text
    assert "cannot certify Caddy" in text
    assert "explicitly authorized maintenance interval" in text
    assert "a recovery incident" in text


def test_mobile_routes_stay_independent():
    text=handoff()
    assert "separate authenticated Tailscale Serve :8443" in text
    mobile=MOBILE.read_text(encoding="utf-8")
    assert "five" in mobile.lower()
    assert "mobile SSO" in mobile


def test_runbook_preserves_hold_and_boot_guard():
    text=MAIN.read_text(encoding="utf-8")
    assert "NOT EXECUTED" in text
    assert "Production authorization stays HOLD" in text
    boot=BOOT.read_text(encoding="utf-8")
    assert "Do not re-enable socat as a security rollback" in boot


def test_no_positive_recovery_claim_requires_raw_forwarding():
    steps=handoff().split("Important:")[0]
    for paragraph in re.split(r"\n(?=\d+\. )",steps):
        if paragraph.startswith(("3. ","4. ","5. ")) and "rollback" in paragraph.lower():
            assert "sanitizing Caddy" in paragraph or "security rollback" in paragraph
