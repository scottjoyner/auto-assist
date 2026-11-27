"""Hermetic regression tests for the voice-action approval gate (sweep27).

The approval gate must fail closed on an unrecognized ``risk_level``: only an
explicitly recognized low-risk level may be auto-approved for an already
trusted actor performing a low-risk action. Any other label (for example a
malformed or unknown value such as ``"critical"``) must require approval.

These tests call a pure function with synthetic strings only. They open no
socket, read no credential/DB/Neo4j/outbox state, and touch no live service.
"""

from __future__ import annotations

import pytest

from assistx.swarm_core import action_requires_approval


@pytest.mark.parametrize(
    "risk_level",
    [
        "critical",
        "severe",
        "bogus-value",
        "medium",
        "high-ish",
    ],
)
def test_unrecognized_risk_level_requires_approval(risk_level: str) -> None:
    """An unknown risk label must never take the auto-approve path."""
    assert action_requires_approval("authenticated_scott", "create_draft_task", risk_level) is True


def test_recognized_low_risk_still_auto_approved() -> None:
    """Control: the documented low-risk path is unchanged."""
    assert action_requires_approval("authenticated_scott", "create_draft_task", "low") is False


def test_high_risk_still_requires_approval() -> None:
    """Control: an explicitly high-risk action still requires approval."""
    assert action_requires_approval("authenticated_scott", "create_draft_task", "high") is True


def test_unknown_speaker_requires_approval_for_unrecognized_risk() -> None:
    """Control: an untrusted actor is never auto-approved."""
    assert action_requires_approval("unknown_speaker", "create_draft_task", "critical") is True


def test_non_low_risk_action_requires_approval_for_unrecognized_risk() -> None:
    """Control: a non-allowlisted action is never auto-approved."""
    assert action_requires_approval("authenticated_scott", "delete_files", "critical") is True


def test_recognized_levels_are_case_and_whitespace_normalized() -> None:
    """Control: normalization of recognized levels is unchanged."""
    assert action_requires_approval("authenticated_scott", "create_draft_task", " LOw ") is False
    assert action_requires_approval("authenticated_scott", "create_draft_task", " High ") is True
