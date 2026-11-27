"""Fail-closed regression tests for the metadata-only secret binding contract (r11).

The contract promises that ``SecretBinding.metadata()`` is the only
representation safe for graph/task/dashboard transport, and that likely secret
material is rejected at construction. The embedded-secret and Bearer-scheme
checks were case-sensitive, so mixed-case spellings were accepted verbatim and
emitted by ``metadata()``. These tests are pure objects: no backend, no
credentials, no network, no live state.
"""

from __future__ import annotations

import pytest

from assistx.secrets import SecretBinding, SecretExposureState, SecretInventory, SecretStatus
from assistx.secrets.refs import _reject_secret_material


def _binding(**overrides):
    base = dict(
        secret_ref="kv://fleet/x1-370/assistx/basic-auth",
        name="assistx-basic-auth",
        owner="assistx-api",
        consumer="x1-370/assistx-api",
        scope="assistx dashboard API",
    )
    base.update(overrides)
    return SecretBinding(**base)


@pytest.mark.parametrize(
    "ref",
    [
        "bearer abc123",
        "BEARER abc123",
        "Bearer abc123",
        "bEaReR\tabc123",
    ],
)
def test_case_variant_bearer_scheme_ref_is_rejected(ref):
    """The HTTP auth scheme is case-insensitive (RFC 7235); all casings fail closed."""
    with pytest.raises(ValueError, match="secret material"):
        _binding(secret_ref=ref)


@pytest.mark.parametrize(
    "ref",
    [
        "Password=abc123",
        "PASSWORD=abc123",
        "TOKEN=abc123",
        "secret: abc123",
        "password = abc123",
        "Bearer=abc123",
    ],
)
def test_case_variant_embedded_assignment_ref_is_rejected(ref):
    with pytest.raises(ValueError, match="must not embed a secret value"):
        _binding(secret_ref=ref)


@pytest.mark.parametrize(
    "ref",
    [
        "kv://Fleet/X1-370/AssistX/basic-auth",
        "vault://SECRETS/NEO4J",
        "opaque://Mixed/Case/Reference",
        "kv://fleet/x1-370/assistx/basic-auth",
    ],
)
def test_legitimate_mixed_case_opaque_refs_are_accepted(ref):
    assert _binding(secret_ref=ref).metadata()["secret_ref"] == ref


@pytest.mark.parametrize(
    "field",
    ["name", "owner", "consumer", "scope", "provenance_ref", "last_verified_at"],
)
def test_lowercase_bearer_marker_is_rejected_in_every_free_text_field(field):
    with pytest.raises(ValueError, match="resembles secret material"):
        _binding(**{field: "bearer real-token-value"})


def test_uppercase_bearer_marker_is_still_rejected_in_scope():
    """Baseline behaviour from the original suite must not regress."""
    with pytest.raises(ValueError, match="secret material"):
        _binding(scope="Bearer actual-token-value")


@pytest.mark.parametrize(
    "material",
    ["ghp_abcdef0123456789", "sk-abcdef0123456789", "-----BEGIN PRIVATE KEY-----"],
)
def test_known_token_prefix_markers_are_rejected(material):
    with pytest.raises(ValueError, match="resembles secret material"):
        _binding(scope=material)


@pytest.mark.parametrize("material", [{}, [], ()])
def test_structured_secret_material_is_rejected(material):
    with pytest.raises(ValueError, match="not structured secret material"):
        _binding(scope=material)


def test_none_optional_fields_are_allowed():
    binding = _binding(provenance_ref=None, last_verified_at=None)
    meta = binding.metadata()
    assert meta["provenance_ref"] is None
    assert meta["last_verified_at"] is None


def test_secret_status_taxonomy_is_pinned():
    assert {s.value for s in SecretStatus} == {"planned", "discovered", "staged", "active", "retired"}


def test_exposure_state_taxonomy_is_pinned():
    assert {s.value for s in SecretExposureState} == {"unknown", "suspected", "confirmed", "remediated"}


def test_defaults_are_contract_values():
    binding = _binding()
    assert binding.backend == "unselected"
    assert binding.status is SecretStatus.DISCOVERED
    assert binding.exposure_state is SecretExposureState.UNKNOWN


def test_metadata_has_exactly_the_transport_safe_keys():
    meta = _binding().metadata()
    assert set(meta) == {
        "secret_ref",
        "name",
        "owner",
        "consumer",
        "scope",
        "backend",
        "status",
        "exposure_state",
        "provenance_ref",
        "last_verified_at",
    }
    assert meta["status"] == "discovered"
    assert meta["exposure_state"] == "unknown"
    assert isinstance(meta["status"], str)
    assert isinstance(meta["exposure_state"], str)
    assert "value" not in meta
    assert "password" not in meta


def test_metadata_never_emits_a_secret_marker():
    meta = _binding(name="assistx-basic-auth", scope="assistx dashboard API").metadata()
    joined = " ".join(str(v) for v in meta.values() if v is not None)
    assert "bearer" not in joined.lower()
    assert "ghp_" not in joined
    assert "sk-" not in joined


def test_direct_reject_helper_is_case_insensitive_for_bearer():
    with pytest.raises(ValueError, match="resembles secret material"):
        _reject_secret_material("bearer real-token-value", "scope")


def test_direct_reject_helper_allows_opaque_reference():
    _reject_secret_material("kv://fleet/x1-370/assistx/basic-auth", "secret_ref")


def test_inventory_deduplicates_same_ref_and_consumer():
    inventory = SecretInventory()
    inventory.add(_binding())
    inventory.add(_binding())
    assert len(inventory.metadata()) == 1


def test_inventory_keeps_same_ref_for_a_different_consumer():
    inventory = SecretInventory()
    inventory.add(_binding())
    inventory.add(_binding(consumer="x2-370/assistx-api"))
    assert len(inventory.metadata()) == 2


def test_inventory_metadata_is_an_independent_snapshot():
    inventory = SecretInventory()
    inventory.add(_binding())
    snapshot = inventory.metadata()
    snapshot.clear()
    assert len(inventory.metadata()) == 1
