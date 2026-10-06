import pytest

from assistx.secrets import SecretBinding, SecretExposureState, SecretInventory


def test_binding_exposes_metadata_only():
    binding = SecretBinding(
        secret_ref="kv://fleet/x1-370/assistx/basic-auth",
        name="assistx-basic-auth",
        owner="assistx-api",
        consumer="x1-370/assistx-api",
        scope="assistx dashboard API",
        exposure_state=SecretExposureState.SUSPECTED,
    )
    metadata = binding.metadata()
    assert metadata["secret_ref"].startswith("kv://")
    assert "password" not in metadata
    assert "value" not in metadata


def test_secret_like_payload_is_rejected():
    with pytest.raises(ValueError, match="secret material"):
        SecretBinding(
            secret_ref="kv://fleet/x1-370/assistx/basic-auth",
            name="assistx-basic-auth",
            owner="assistx-api",
            consumer="x1-370/assistx-api",
            scope="Bearer actual-token-value",
        )


def test_inventory_deduplicates_same_consumer_reference():
    def make_binding():
        return SecretBinding(
            secret_ref="kv://fleet/x1-370/neo4j",
            name="neo4j-auth",
            owner="neo4j",
            consumer="x1-370",
            scope="bolt authentication",
        )

    inventory = SecretInventory()
    inventory.add(make_binding())
    inventory.add(make_binding())
    assert len(inventory.metadata()) == 1
