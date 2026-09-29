from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

import pytest

from assistx import control_room


def test_classify_runtime_mode() -> None:
    assert control_room.classify_runtime_mode("LM Studio 0.4") == "LM_STUDIO"
    assert control_room.classify_runtime_mode("llama.cpp server") == "HEADLESS"
    assert control_room.classify_runtime_mode("custom", True) == "HEADLESS"
    assert control_room.classify_runtime_mode("custom", False) == "LM_STUDIO"
    assert control_room.classify_runtime_mode(None) == "UNKNOWN"


def test_human_activity_title_prefers_task_context() -> None:
    assert control_room.human_activity_title("Fix router timeout", "diagnosis", "abc") == "Fix router timeout"
    assert control_room.human_activity_title(None, "repo_analysis", "abc") == "Repo analysis"
    assert control_room.human_activity_title(None, None, "1234567890") == "Task 12345678"


def test_collect_runtimes_keeps_paths_under_one_slot_pool(monkeypatch) -> None:
    monkeypatch.setattr(
        control_room,
        "_runtime_inventory",
        lambda _factory: [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "node_id": "xwing",
                "runtime_kind": "lmstudio",
                "runtime_version": "0.4.7",
                "headless": False,
                "loaded_models": [{"model_key": "qwen/test", "quantization": "Q4"}],
            }
        ],
    )
    monkeypatch.setattr(
        control_room,
        "_endpoint_inventory",
        lambda _factory: [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "node_id": "xwing",
                "base_url": "http://192.168.1.9:1234/v1",
            }
        ],
    )
    admission = {
        "runtimes": [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "parallel_slots": 1,
                "active": 1,
                "queued": 2,
                "queue_limit": 4,
            }
        ],
        "access_paths": [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "approved_access_urls": [
                    "http://192.168.1.9:1234/v1",
                    "http://100.64.0.9:1234/v1",
                ],
                "selected_access_url": "http://192.168.1.9:1234/v1",
                "selected_transport": "lan",
                "selection_fresh": True,
            }
        ],
    }

    runtimes = control_room.collect_runtimes(lambda: None, admission)

    assert len(runtimes) == 1
    runtime = runtimes[0]
    assert runtime["runtime_instance_id"] == "lmstudio-xwing-1234"
    assert runtime["runtime_mode"] == "LM_STUDIO"
    assert runtime["parallel_slots"] == 1
    assert runtime["active"] == 1
    assert runtime["queued"] == 2
    assert runtime["selected_transport"] == "lan"
    assert len(runtime["access_paths"]) == 2


def test_build_overview_fails_closed_on_required_dependency(monkeypatch) -> None:
    monkeypatch.setattr(
        control_room,
        "collect_dependencies",
        lambda _factory: (
            [
                {
                    "name": "Neo4j",
                    "required": True,
                    "status": "unhealthy",
                }
            ],
            {},
        ),
    )
    monkeypatch.setattr(control_room, "collect_runtimes", lambda _factory, _admission: [])
    monkeypatch.setattr(control_room, "collect_activity", lambda _factory: [])
    monkeypatch.setattr(control_room, "collect_performance", lambda _factory: [])
    monkeypatch.setattr(control_room, "collect_fleet_nodes", lambda _factory: [])
    monkeypatch.setattr(control_room, "collect_power", lambda: {})
    monkeypatch.setattr(control_room, "_load_doctor_findings", lambda: {})
    monkeypatch.setattr(control_room, "island_recovery_snapshot", lambda: {})

    snapshot = control_room.build_overview(lambda: None)

    assert snapshot["overall_status"] == "degraded"
    assert snapshot["summary"]["required_dependency_failures"] == 1


def test_legacy_operator_pages_are_consolidated() -> None:
    # Only true duplicates stay consolidated; operator pages render again.
    assert "/" in control_room.LEGACY_UI_PATHS
    assert "/command-center" in control_room.LEGACY_UI_PATHS
    assert "/fleet" in control_room.LEGACY_UI_PATHS
    for revived in ("/live", "/operations", "/fleet-dashboard", "/strategy", "/routing"):
        assert revived not in control_room.LEGACY_UI_PATHS


def test_collect_runtimes_drops_expired_loaded_models(monkeypatch) -> None:
    now = control_room._now_ms()
    monkeypatch.setattr(
        control_room,
        "_runtime_inventory",
        lambda _factory: [
            {
                "runtime_instance_id": "lmstudio-xwing-1234",
                "node_id": "xwing",
                "runtime_kind": "lmstudio",
                "status": "online",
                "loaded_models": [
                    {"model_key": "fresh/model", "expires_at_ts": now + 60_000},
                    {"model_key": "expired/model", "expires_at_ts": now - 60_000},
                    {"model_key": "expired/older", "expires_at_ts": now - 10**9},
                ],
            }
        ],
    )
    monkeypatch.setattr(control_room, "_endpoint_inventory", lambda _factory: [])

    runtimes = control_room.collect_runtimes(lambda: None, {})

    assert len(runtimes) == 1
    runtime = runtimes[0]
    assert [m["model_key"] for m in runtime["loaded_models"]] == ["fresh/model"]
    assert runtime["stale_loaded_models"] == 2


def test_collect_runtimes_endpoint_library_scan_is_not_loaded_models(monkeypatch) -> None:
    # models_json holds the node's downloaded library (on-disk paths from a
    # scan) — it is catalog data and must never render as loaded models.
    monkeypatch.setattr(control_room, "_runtime_inventory", lambda _factory: [])
    monkeypatch.setattr(
        control_room,
        "_endpoint_inventory",
        lambda _factory: [
            {
                "runtime_instance_id": "lmstudio-deathstar-1234",
                "node_id": "deathstar-xps-8920",
                "base_url": "http://100.78.106.121:1234/v1",
                "models_json": json.dumps([
                    {"model_id": "/mnt/8TB/apps/lmstudio/models/VibeThinker-3B-Hermes-v04-Q8_0.gguf"}
                ]),
            }
        ],
    )

    runtimes = control_room.collect_runtimes(lambda: None, {})

    assert len(runtimes) == 1
    runtime = runtimes[0]
    assert runtime["loaded_models"] == []
    assert runtime["catalog_models"][0]["model_key"].endswith(".gguf")


class _FakeNeo:
    def __init__(self, records: list[dict]) -> None:
        self.driver = MagicMock()
        self._session = MagicMock()
        self.driver.session.return_value.__enter__ = MagicMock(return_value=self._session)
        self.driver.session.return_value.__exit__ = MagicMock(return_value=False)
        self.closed = False
        runs = []
        for record in records:
            run = MagicMock()
            run.data.return_value = record if isinstance(record, list) else [record]
            runs.append(run)
        self._session.run.side_effect = runs

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def _fleet_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSISTX_FLEET_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(control_room, "_FLEET_NODES_CACHE", {"ts": 0.0, "data": []})
    (tmp_path / "node-hardware.json").write_text(json.dumps({"nodes": [
        {
            "node_id": "mac-air",
            "tailscale_ip": "100.64.0.5",
            "hostname_display": "scotts-macbook-air",
            "instrument": {"npu": False, "iGPU": {"on": True, "model": "M2 GPU"}, "dGPU": {"on": False, "model": None}},
            "load_tag": "fast",
            "slot_cap": 2,
            "role": ["worker"],
        },
        {
            "node_id": "joyner",
            "tailscale_ip": "100.83.215.83",
            "hostname_display": "joyner",
            "instrument": {"npu": False, "iGPU": {"on": False, "model": None}, "dGPU": {"on": False, "model": None}},
            "note": "LAN-only (192.168.1.79), no key auth yet. Specs TBD.",
        },
    ]}))
    (tmp_path / "fleet-expected-nodes.json").write_text(json.dumps({"nodes": [
        {"node_id": "mac-air", "ip": "100.64.0.5", "hostname": "scotts-macbook-air", "role": "worker"},
        {"node_id": "joyner", "ip": "100.83.215.83", "hostname": "joyner", "role": "tank-recovery-provider"},
    ]}))
    return tmp_path


def _registry_payload() -> dict:
    return {"nodes": [
        {"ip": "100.64.0.5", "hostname": "air-registry-label", "received_at": 0,
         "loaded": ["/mnt/models/Qwen3-4B-Q8_0.gguf"], "specs": {"gpu": "M2 GPU"}},
        {"ip": "100.83.215.83", "hostname": "joyner", "received_at": 0, "loaded": []},
    ]}


def _open_registry(monkeypatch, payload: dict) -> list[str]:
    requested: list[str] = []

    def _fake_urlopen(url, timeout=0):
        requested.append(url)
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(control_room, "urlopen", _fake_urlopen)
    monkeypatch.setattr(control_room, "_router_url", lambda path, env=None: f"http://router.test/{path}")
    return requested


def test_collect_fleet_nodes_live_none_uses_router_and_marks_unknown(_fleet_env, monkeypatch) -> None:
    monkeypatch.setattr(control_room, "_live_loaded_models", lambda _f: None)
    monkeypatch.setattr(control_room, "_historical_loaded_models", lambda _f: {})
    monkeypatch.setattr(control_room, "_provider_catalog", lambda _f=None: {})
    _open_registry(monkeypatch, _registry_payload())

    nodes = control_room.collect_fleet_nodes(lambda: None)

    by_ip = {node["ip"]: node for node in nodes}
    air = by_ip["100.64.0.5"]
    assert air["loaded_models"] == ["Qwen3-4B-Q8_0"]
    assert air["model_source"] == "router"
    assert air["instrument"] == "iGPU:M2 GPU"
    joyner = by_ip["100.83.215.83"]
    assert joyner["loaded_models"] == []
    assert joyner["instrument"] == "UNKNOWN"


def test_collect_fleet_nodes_live_empty_overrides_router_fallback(_fleet_env, monkeypatch) -> None:
    monkeypatch.setattr(control_room, "_live_loaded_models", lambda _f: {})
    monkeypatch.setattr(control_room, "_historical_loaded_models", lambda _f: {})
    monkeypatch.setattr(control_room, "_provider_catalog", lambda _f=None: {})
    _open_registry(monkeypatch, _registry_payload())

    nodes = control_room.collect_fleet_nodes(lambda: None)

    by_ip = {node["ip"]: node for node in nodes}
    air = by_ip["100.64.0.5"]
    assert air["loaded_models"] == []
    assert air["model_source"] == "live-empty"
    assert air["model_clue"] == "live graph reports no loaded models"
    assert "no live graph data" not in (air["model_clue"] or "")


def test_collect_fleet_nodes_matches_registry_by_ip_not_label(_fleet_env, monkeypatch) -> None:
    monkeypatch.setattr(control_room, "_live_loaded_models", lambda _f: {
        "mac-air": {"models": ["ornith-4b"], "online": True, "observed_at": "123", "latencies": {}},
    })
    monkeypatch.setattr(control_room, "_historical_loaded_models", lambda _f: {})
    monkeypatch.setattr(control_room, "_provider_catalog", lambda _f=None: {})
    _open_registry(monkeypatch, _registry_payload())

    nodes = control_room.collect_fleet_nodes(lambda: None)

    by_ip = {node["ip"]: node for node in nodes}
    air = by_ip["100.64.0.5"]
    assert len(nodes) == 2
    assert air["canonical_name"] == "scotts-macbook-air"
    assert air["loaded_models"] == ["ornith-4b"]
    assert air["model_source"] == "live"


def test_collect_fleet_nodes_manifest_primary_is_inferred(_fleet_env, monkeypatch) -> None:
    hw = json.loads((_fleet_env / "node-hardware.json").read_text())
    hw["nodes"][0]["primary_model"] = "/mnt/models/ornith-14b.gguf"
    (_fleet_env / "node-hardware.json").write_text(json.dumps(hw))
    monkeypatch.setattr(control_room, "_live_loaded_models", lambda _f: None)
    monkeypatch.setattr(control_room, "_historical_loaded_models", lambda _f: {})
    monkeypatch.setattr(control_room, "_provider_catalog", lambda _f=None: {})
    _open_registry(monkeypatch, {"nodes": []})

    nodes = control_room.collect_fleet_nodes(lambda: None)

    air = next(node for node in nodes if node["ip"] == "100.64.0.5")
    assert air["loaded_models"] == ["ornith-14b"]
    assert air["model_source"] == "manifest-primary"
    assert air["opacity"] == "inferred"


def test_collect_fleet_nodes_uses_exact_provider_endpoint_override(_fleet_env, monkeypatch) -> None:
    seen: list[tuple[str, str]] = []

    def _fake_http_json(url, path, **_kwargs):
        seen.append((url, path))
        return {"providers": [{"enabled": True, "node_id": "mac-air", "models": [{"provider_model": "ornith-4b"}]}]}, 1.0

    monkeypatch.setattr(control_room, "_http_json", _fake_http_json)
    monkeypatch.setattr(control_room, "_live_loaded_models", lambda _f: None)
    monkeypatch.setattr(control_room, "_historical_loaded_models", lambda _f: {})
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_PROVIDERS_URL", "http://router.test:9090/admin/providers")
    _open_registry(monkeypatch, {"nodes": []})

    nodes = control_room.collect_fleet_nodes(lambda: None)

    assert seen == [("http://router.test:9090/admin/providers", "")]
    air = next(node for node in nodes if node["ip"] == "100.64.0.5")
    assert air["available_models"] == ["ornith-4b"]


def test_live_loaded_models_none_on_neo_failure() -> None:
    def _broken_factory():
        raise RuntimeError("no database")

    assert control_room._live_loaded_models(_broken_factory) is None
    assert control_room._live_loaded_models(None) is None


def test_live_loaded_models_ignores_unconfirmed_model_states() -> None:
    fake = _FakeNeo([
        {"ms": 111},
        {"node": "mac-air", "states": [
            {"model_id": "ornith-4b", "online": True, "loaded": True, "inventory_authoritative": True},
            {"model_id": "qwen-8b", "online": True, "loaded": False, "inventory_authoritative": False},
        ]},
    ])

    result = control_room._live_loaded_models(lambda: fake)

    assert result == {
        "mac-air": {"models": ["ornith-4b"], "online": True, "observed_at": "111", "latencies": {}},
    }
    assert fake.closed


def test_historical_loaded_models_uses_immutable_observations() -> None:
    fake = _FakeNeo([
        {"node": "mac-air", "snap": 50, "models": ["ornith-4b"]},
    ])

    result = control_room._historical_loaded_models(lambda: fake)

    query = fake._session.run.call_args[0][0]
    assert "HAS_MODEL_OBSERVATION" in query
    assert "FleetModelObservation" in query
    assert "HAS_MODEL_STATE" not in query
    assert result["mac-air"]["models"] == ["ornith-4b"]
    assert fake.closed


def test_historical_loaded_models_empty_on_neo_failure() -> None:
    def _broken_factory():
        raise RuntimeError("no database")

    assert control_room._historical_loaded_models(_broken_factory) == {}

