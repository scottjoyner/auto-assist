"""Machine-readable reproducibility manifests for AssistX experiments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExperimentManifest:
    name: str
    variant: str
    baseline_id: str
    source_repository: str
    source_commit: str
    corpus_id: str
    config: dict[str, Any] = field(default_factory=dict)
    upstream_repository: str | None = None
    upstream_version: str | None = None
    model_hash: str | None = None
    quant: str | None = None
    runtime_id: str | None = None
    node_id: str | None = None
    privacy_class: str = "local_only"

    def validate(self) -> None:
        required = {
            "name": self.name,
            "variant": self.variant,
            "baseline_id": self.baseline_id,
            "source_repository": self.source_repository,
            "source_commit": self.source_commit,
            "corpus_id": self.corpus_id,
            "privacy_class": self.privacy_class,
        }
        for field_name, value in required.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if (self.upstream_repository is None) != (self.upstream_version is None):
            raise ValueError("upstream_repository and upstream_version must be supplied together")

    def canonical_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def manifest_id(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def trace_metadata(self) -> dict[str, Any]:
        upstream = None
        if self.upstream_repository is not None:
            upstream = {
                "repository": self.upstream_repository,
                "version": self.upstream_version,
            }
        return {
            "name": self.name,
            "variant": self.variant,
            "baseline_id": self.baseline_id,
            "source_commit": self.source_commit,
            "manifest_id": self.manifest_id,
            "upstream": upstream,
        }
