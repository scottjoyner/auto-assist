"""Safe, metadata-only secret binding models.

The types in this module intentionally reject likely secret material. They are for
inventory and policy planning; a backend resolver is deliberately not implemented.
"""

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Any


class SecretStatus(StrEnum):
    PLANNED = "planned"
    DISCOVERED = "discovered"
    STAGED = "staged"
    ACTIVE = "active"
    RETIRED = "retired"


class SecretExposureState(StrEnum):
    UNKNOWN = "unknown"
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"
    REMEDIATED = "remediated"


_SENSITIVE_FIELD = re.compile(r"^(password|passwd|secret_value|token_value|api[_-]?key|private[_-]?key|credential_value|bearer)$", re.I)


def _reject_secret_material(value: Any, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, (dict, list, tuple)):
        raise ValueError(f"{field_name} must be an opaque reference, not structured secret material")
    text = str(value)
    if _SENSITIVE_FIELD.search(field_name) and len(text) > 0:
        raise ValueError(f"{field_name} must not contain secret material")
    if any(marker in text for marker in ("-----BEGIN", "Bearer ", "ghp_", "sk-")):
        raise ValueError(f"{field_name} resembles secret material")


@dataclass(frozen=True)
class SecretBinding:
    secret_ref: str
    name: str
    owner: str
    consumer: str
    scope: str
    backend: str = "unselected"
    status: SecretStatus = SecretStatus.DISCOVERED
    exposure_state: SecretExposureState = SecretExposureState.UNKNOWN
    provenance_ref: str | None = None
    last_verified_at: str | None = None

    def __post_init__(self) -> None:
        for key, value in self.__dict__.items():
            _reject_secret_material(value, key)
        if not self.secret_ref.strip():
            raise ValueError("secret_ref must be a non-empty opaque reference")
        if self.secret_ref.startswith(("password=", "token=", "secret=", "Bearer ")):
            raise ValueError("secret_ref must not embed a secret value")

    def metadata(self) -> dict[str, Any]:
        """Return the only representation safe for graph/task/dashboard transport."""
        return {
            "secret_ref": self.secret_ref,
            "name": self.name,
            "owner": self.owner,
            "consumer": self.consumer,
            "scope": self.scope,
            "backend": self.backend,
            "status": self.status.value,
            "exposure_state": self.exposure_state.value,
            "provenance_ref": self.provenance_ref,
            "last_verified_at": self.last_verified_at,
        }


@dataclass
class SecretInventory:
    bindings: list[SecretBinding] = field(default_factory=list)

    def add(self, binding: SecretBinding) -> None:
        if any(existing.secret_ref == binding.secret_ref and existing.consumer == binding.consumer for existing in self.bindings):
            return
        self.bindings.append(binding)

    def metadata(self) -> list[dict[str, Any]]:
        return [binding.metadata() for binding in self.bindings]
