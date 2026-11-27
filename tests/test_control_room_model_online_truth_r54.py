"""Fail-closed control-room live-model online verdict (round 54).

The live-graph branch of ``collect_fleet_nodes`` copied ``online`` straight from
the observation entry with a ``True`` default.  A snapshot whose online fact was
absent, non-boolean, or null was therefore rendered as a confirmed-online model.
This module pins the verdict to an explicit boolean ``True`` and proves the old
behaviour fails closed.

Pure in-process test: dict fixtures, stubbed readers, no socket, DB, service,
credential, filesystem write outside pytest's tmp_path, or network mount.
"""

from __future__ import annotations

import io
import json

import pytest

from assistx import control_room


@pytest.fixture()
def _r54_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSISTX_FLEET_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(control_room, "_FLEET_NODES_CACHE", {"ts": 0.0, "data": []})
    (tmp_path / "node-hardware.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "node_id": "mac-air",
                        "tailscale_ip": "100.64.0.5",
                        "hostname_display": "scotts-macbook-air",
                        "instrument": {},
                        "load_tag": "fast",
                        "slot_cap": 2,
                        "role": ["worker"],
                    }
                ]
            }
        )
    )
    (tmp_path / "fleet-expected-nodes.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "node_id": "mac-air",
                        "ip": "100.64.0.5",
                        "hostname": "scotts-macbook-air",
                        "role": "worker",
                    }
                ]
            }
        )
    )
    return tmp_path


def _collect_with_live(monkeypatch, live_entry):
    monkeypatch.setattr(
        control_room, "_live_loaded_models", lambda _factory: {"mac-air": live_entry}
    )
    monkeypatch.setattr(control_room, "_historical_loaded_models", lambda _factory: {})
    monkeypatch.setattr(control_room, "_provider_catalog", lambda _factory=None: {})

    def _fake_urlopen(_url, timeout=0):
        return io.BytesIO(json.dumps({"nodes": []}).encode("utf-8"))

    monkeypatch.setattr(control_room, "urlopen", _fake_urlopen)
    monkeypatch.setattr(
        control_room,
        "_router_url",
        lambda path, env=None: "http://router.test/" + path,
    )
    return control_room.collect_fleet_nodes(lambda: None)


@pytest.mark.parametrize(
    "online_field, expected",
    [
        pytest.param({"online": True}, True, id="legit-boolean-true-preserved"),
        pytest.param({"online": False}, False, id="legit-boolean-false-preserved"),
        pytest.param({}, False, id="missing-online-fact-fails-closed"),
        pytest.param({"online": "false"}, False, id="string-false-fails-closed"),
        pytest.param({"online": "no"}, False, id="string-no-fails-closed"),
        pytest.param({"online": 1}, False, id="int-one-fails-closed"),
        pytest.param({"online": None}, False, id="null-fails-closed"),
        pytest.param({"online": []}, False, id="empty-list-fails-closed"),
    ],
)
def test_live_model_online_requires_explicit_boolean(
    _r54_state_dir, monkeypatch, online_field, expected
):
    live_entry = {"models": ["ornith-4b"], "observed_at": "123", **online_field}
    nodes = _collect_with_live(monkeypatch, live_entry)

    assert len(nodes) == 1
    node = nodes[0]
    assert node["model_source"] == "live"
    assert node["loaded_models"] == ["ornith-4b"]
    assert node["model_online"] is expected
