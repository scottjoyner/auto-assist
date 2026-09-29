"""Normalized evidence helpers for non-authoritative shadow experiments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ShadowEvidence:
    experiment: str
    variant: str
    authoritative_behavior_changed: bool
    metrics: dict[str, Any]
    source_commit: str | None = None

    def as_trace_attributes(self) -> dict[str, Any]:
        return {
            "experiment": self.experiment,
            "variant": self.variant,
            "authoritative_behavior_changed": self.authoritative_behavior_changed,
            "source_commit": self.source_commit,
            "metrics": dict(self.metrics),
        }


def make_shadow_evidence(
    experiment: str,
    *,
    metrics: dict[str, Any],
    source_commit: str | None = None,
    variant: str = "shadow",
) -> ShadowEvidence:
    if not experiment.strip():
        raise ValueError("experiment is required")
    if not variant.strip():
        raise ValueError("variant is required")
    return ShadowEvidence(
        experiment=experiment,
        variant=variant,
        authoritative_behavior_changed=False,
        metrics=dict(metrics),
        source_commit=source_commit,
    )
