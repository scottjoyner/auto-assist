"""Durable JSON artifact serialization for experiment evidence and promotion decisions."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def write_experiment_artifact(
    path: str | Path,
    *,
    manifest: Any,
    comparison: dict[str, Any],
    promotion: Any,
    metadata: dict[str, Any] | None = None,
) -> str:
    payload = {
        "schema_version": "assistx.experiment-artifact.v1",
        "manifest": _normalize(manifest),
        "comparison": _normalize(comparison),
        "promotion": _normalize(promotion),
        "metadata": _normalize(metadata or {}),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return str(target)
