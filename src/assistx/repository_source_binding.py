"""Serialization seam between a source binding and a provenance document.

Constructing and verifying bindings lives in ``repository_source_verifier``.
What remains here is the transport shape: the dict that travels on a task, and
the fail-closed parse that turns one back into a binding.

Deliberately no ``schema_version`` key. The models in
``contracts.schemas.repository_source_binding`` set ``extra="forbid"``, so a
document carrying an extra key cannot be read back -- adding one here would
make every binding fail to parse on the way in.
"""

from __future__ import annotations

from typing import Any

from .contracts.schemas.repository_source_binding import RepositorySourceBinding

__all__ = ["binding_to_document", "document_to_binding"]


def binding_to_document(binding: RepositorySourceBinding) -> dict[str, Any]:
    """Serialize a binding for provenance on a task or result."""
    return binding.model_dump(mode="json")


def document_to_binding(document: dict[str, Any]) -> RepositorySourceBinding:
    """Rebuild a binding from a provenance document, fail-closed."""
    return RepositorySourceBinding.model_validate(document)
