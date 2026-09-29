from __future__ import annotations

import pytest

from assistx.llm import client as llm_client


REQUIRED_LOADER_STATE_KEYS = {
    "running",
    "last_run_ts",
    "last_action",
    "cycle",
    "discovered_models",
    "owners",
    "per_node",
    "pinned",
}


def test_loader_state_is_initialized_with_every_key_the_loop_touches():
    state = llm_client.get_loader_state()
    assert not state.get("stale"), "loader state lock was not acquirable"
    assert REQUIRED_LOADER_STATE_KEYS <= set(state)


def test_loader_cycle_counter_increments_without_keyerror():
    state = llm_client.get_loader_state()
    before = state["cycle"]
    llm_client._loader_state["cycle"] += 1
    after = llm_client.get_loader_state()
    assert after["cycle"] == before + 1


def test_pinned_models_are_reported_as_a_list():
    llm_client.set_loader_wishlist([])
    state = llm_client.get_loader_state()
    assert state["pinned"] == []
    assert isinstance(state["last_action"], str)


@pytest.mark.parametrize(
    ("key", "empty"),
    [
        ("discovered_models", []),
        ("owners", {}),
        ("per_node", {}),
    ],
)
def test_optional_loader_collections_default_to_empty_containers(key, empty):
    state = llm_client.get_loader_state()
    assert state[key] == empty
