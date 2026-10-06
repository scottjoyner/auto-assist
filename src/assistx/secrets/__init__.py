"""Metadata-only secret inventory primitives.

This package deliberately does not resolve or store secret values.
"""

from .refs import SecretBinding, SecretExposureState, SecretInventory, SecretStatus

__all__ = [
    "SecretBinding",
    "SecretExposureState",
    "SecretInventory",
    "SecretStatus",
]
