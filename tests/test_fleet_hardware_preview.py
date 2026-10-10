"""Synthetic-only tests: never commit real private fleet identities or network addresses."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx.fleet_hardware_preview import (
    FleetHardwareEvidenceError,
    build_fleet_hardware_preview_router,
    hardware_preview,
)

SNAP = "2026-10-10T16-00-00Z"
NOW = dt.datetime(2026, 10, 10, 17, 0, tzinfo=dt.UTC)


def _write(root: Path, rel: str, data: object) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True))


@pytest.fixture
def export(tmp_path: Path) -> Path:
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots/LATEST").write_text(SNAP + "\n")
    _write(
        tmp_path,
        "inventory/tailscale-nodes.json",
        {
            "source_snapshot": SNAP,
            "nodes": [{"name": "gpu-node"}, {"name": "nas-node"}, {"name": "offline-node"}],
        },
    )
    _write(
        tmp_path,
        f"snapshots/{SNAP}/probe-status.json",
        {
            "snapshot": SNAP,
            "results": {
                "gpu-node": "verified",
                "nas-node": "verified",
                "offline-node": "ssh_authentication_denied",
            },
        },
    )
    _write(
        tmp_path,
        "inventory/fleet-resources.json",
        {
            "snapshot": SNAP,
            "read_only_advisory": True,
            "automated_admission_allowed": False,
            "nodes": {
                "gpu-node": {
                    "admission_status": "ADVISORY_ONLY_NO_DISPATCH",
                    "observed_snapshot": SNAP,
                    "os_visible_ram_gib": 48.0,
                    "gpu_functions": [
                        {"pci_bdf": "0000:01:00.0", "driver_vram_gib": 20.0, "shared_gtt_gib": 64.0},
                        {"pci_bdf": "0000:02:00.0", "driver_vram_gib": 2.0, "shared_gtt_gib": 64.0},
                    ],
                    "risk_flags": [{"code": "SHARED_USB_ROOT_BUS", "drives": ["/dev/secret"]}],
                },
                "nas-node": {
                    "admission_status": "ADVISORY_ONLY_NO_DISPATCH",
                    "observed_snapshot": SNAP,
                    "os_visible_ram_gib": 8.0,
                    "gpu_functions": [],
                    "risk_flags": [{"code": "NAS_WIRED_UPLINK_1G"}],
                },
                "offline-node": {"admission_status": "NO_VERIFIED_HARDWARE"},
            },
        },
    )
    _write(
        tmp_path,
        "inventory/fleet-topology.json",
        {
            "snapshot": SNAP,
            "edges": [
                {
                    "from": "mount/gpu-node/remote",
                    "to": "host/nas-node",
                    "relation": "REMOTE_MOUNT_TO_HOST",
                    "endpoint_ip": "192.0.2.100",
                },
                {"from": "block/gpu-node/dev/sda", "to": "usbroot/gpu-node/usb1", "relation": "UPSTREAM_USB_ROOT"},
            ],
        },
    )
    return tmp_path


def test_static_gpu_function_locality_and_no_dispatch(export: Path) -> None:
    response = hardware_preview(export, now=NOW, min_ram_gib=16.0, min_gpu_vram_gib=16.0, data_host="nas-node")
    assert response["snapshot_freshness"] == "FRESH"
    assert response["dispatch_allowed"] is False
    assert response["admission_allowed"] is False
    assert response["checked_nodes"] == 3
    node = response["nodes"][0]
    assert node["node_id"] == "gpu-node"
    assert node["static_evidence_state"] == "PLAUSIBLE_NOT_ADMISSIBLE"
    assert node["reported_gpu_pci_functions_matching_request"] == ["0000:01:00.0"]
    assert "REMOTE_MOUNT_TO_DATA_HOST" in node["evidence"]
    assert node["dispatch"] == "DENIED_NO_AUTHORITY"
    offline = next(row for row in response["nodes"] if row["node_id"] == "offline-node")
    assert offline["verification"] == "UNKNOWN"
    assert "UNVERIFIED_HARDWARE" in offline["blockers"]
    assert "192.0.2.100" not in json.dumps(response)
    assert "/dev/secret" not in json.dumps(response)
    assert "64.0" not in json.dumps(response)


def test_freshness_disables_plausibility(export: Path) -> None:
    response = hardware_preview(export, now=NOW + dt.timedelta(days=3), min_ram_gib=16)
    assert response["snapshot_freshness"] == "STALE"
    assert all("STALE_SNAPSHOT" in n["blockers"] for n in response["nodes"])
    assert response["nodes"][0]["static_evidence_state"] == "INCOMPLETE"


def test_unknown_data_host_is_rejected(export: Path) -> None:
    with pytest.raises(ValueError, match="unknown data host"):
        hardware_preview(export, now=NOW, data_host="unknown")


def test_unverified_data_host_cannot_prove_locality(export: Path) -> None:
    response = hardware_preview(export, now=NOW, data_host="offline-node")
    assert all("DATA_HOST_NOT_VERIFIED" in n["blockers"] for n in response["nodes"])


def test_denies_cross_source_version_disagreement(export: Path) -> None:
    p = export / "inventory/fleet-topology.json"
    item = json.loads(p.read_text())
    item["snapshot"] = "2020-01-01T00-00-00Z"
    p.write_text(json.dumps(item))
    with pytest.raises(FleetHardwareEvidenceError, match="snapshot disagreement"):
        hardware_preview(export, now=NOW)


def test_denies_unknown_node_promoted_to_verified(export: Path) -> None:
    p = export / "inventory/fleet-resources.json"
    item = json.loads(p.read_text())
    item["nodes"]["offline-node"]["admission_status"] = "ADVISORY_ONLY_NO_DISPATCH"
    p.write_text(json.dumps(item))
    with pytest.raises(FleetHardwareEvidenceError, match="verification mismatch"):
        hardware_preview(export, now=NOW)


def test_denies_source_with_automatic_admission(export: Path) -> None:
    p = export / "inventory/fleet-resources.json"
    item = json.loads(p.read_text())
    item["automated_admission_allowed"] = True
    p.write_text(json.dumps(item))
    with pytest.raises(FleetHardwareEvidenceError, match="authorization mismatch"):
        hardware_preview(export, now=NOW)


def test_denies_symlinked_file(export: Path) -> None:
    source = export / "inventory/fleet-resources.json"
    copy = export / "inventory/copy.json"
    copy.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(copy)
    with pytest.raises(FleetHardwareEvidenceError, match="symlink"):
        hardware_preview(export, now=NOW)


def test_digest_is_stable_and_changes_on_input_change(export: Path) -> None:
    before = hardware_preview(export, now=NOW)["evidence_digest_sha256"]
    assert before == hardware_preview(export, now=NOW)["evidence_digest_sha256"]
    p = export / "inventory/fleet-resources.json"
    item = json.loads(p.read_text())
    item["nodes"]["gpu-node"]["risk_flags"].append({"code": "NEW_BUS_RISK"})
    p.write_text(json.dumps(item))
    assert hardware_preview(export, now=NOW)["evidence_digest_sha256"] != before


def test_authenticated_route_is_opt_in_and_read_only(export: Path, monkeypatch) -> None:
    def protect():
        raise HTTPException(401, "unauthorized")

    app = FastAPI()
    app.include_router(build_fleet_hardware_preview_router(auth_dependency=protect, root_provider=lambda: str(export)))
    client = TestClient(app)
    response = client.get("/api/fleet/hardware-preview")
    assert response.status_code == 401
    # API has no POST route, and should never be a dispatch endpoint.
    assert client.post("/api/fleet/hardware-preview").status_code == 405

    def approved():
        return "operator"

    app2 = FastAPI()
    app2.include_router(
        build_fleet_hardware_preview_router(auth_dependency=approved, root_provider=lambda: str(export))
    )
    response = TestClient(app2).get("/api/fleet/hardware-preview?ram_gib=16&gpu_vram_gib=16")
    assert response.status_code == 200
    data = response.json()
    assert data["nodes"][0]["node_id"] == "gpu-node"
    assert data["dispatch_allowed"] is False
    assert len(data["evidence_digest_sha256"]) == 64
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["vary"] == "Authorization"


def test_unconfigured_or_corrupt_source_fails_closed(export: Path) -> None:
    app = FastAPI()
    app.include_router(
        build_fleet_hardware_preview_router(auth_dependency=lambda: "operator", root_provider=lambda: "")
    )
    assert TestClient(app).get("/api/fleet/hardware-preview").status_code == 503

    (export / "inventory/fleet-resources.json").write_text("not json")
    app = FastAPI()
    app.include_router(
        build_fleet_hardware_preview_router(auth_dependency=lambda: "operator", root_provider=lambda: str(export))
    )
    resp = TestClient(app).get("/api/fleet/hardware-preview")
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"]
    assert str(export) not in str(resp.json())


def test_request_is_strictly_bounded(export: Path) -> None:
    app = FastAPI()
    app.include_router(
        build_fleet_hardware_preview_router(auth_dependency=lambda: "operator", root_provider=lambda: str(export))
    )
    client = TestClient(app)
    assert client.get("/api/fleet/hardware-preview?gpu_vram_gib=5000").status_code == 422
    assert client.get("/api/fleet/hardware-preview?limit=1000").status_code == 422
    assert client.get("/api/fleet/hardware-preview?data_host=not-present").status_code == 422
