from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def test_tailnet_discovery_prefers_lan_and_keeps_remote_candidates(tmp_path: Path) -> None:
    status = {
        "Self": {
            "ID": "self-id",
            "HostName": "control-host",
            "DNSName": "control-host.example.ts.net.",
            "TailscaleIPs": ["100.64.0.10"],
            "Online": True,
            "Active": True,
        },
        "Peer": {
            "remote-id": {
                "ID": "remote-id",
                "HostName": "xwing",
                "DNSName": "xwing.example.ts.net.",
                "TailscaleIPs": ["100.90.80.70"],
                "Online": True,
                "Active": False,
            },
            "offline-id": {
                "ID": "offline-id",
                "HostName": "offline-node",
                "DNSName": "offline-node.example.ts.net.",
                "TailscaleIPs": ["100.90.80.71"],
                "Online": False,
                "Active": False,
            },
        },
    }
    lan_map = {"xwing": ["http://192.168.1.51:1234/v1"]}
    status_path = tmp_path / "tailscale-status.json"
    lan_map_path = tmp_path / "lan-map.json"
    output_path = tmp_path / "candidates.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    lan_map_path.write_text(json.dumps(lan_map), encoding="utf-8")

    script = Path(__file__).resolve().parents[1] / "scripts" / "reconciliation-discover-tailnet.py"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--input",
            str(status_path),
            "--lan-map",
            str(lan_map_path),
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["authority"] == "candidate_reachability_only"
    assert payload["lan_preferred"] is True
    assert payload["tailscale_fallback"] is True

    nodes = {node["node_id"]: node for node in payload["nodes"]}
    remote = nodes["xwing"]
    assert remote["online"] is True
    assert remote["admission_status"] == "candidate_only"
    assert remote["candidate_access_paths"][0] == {
        "transport": "lan",
        "base_url": "http://192.168.1.51:1234/v1",
        "priority": 10,
        "source_kind": "operator_lan_map",
    }
    assert any(
        path["transport"] == "tailscale"
        and path["base_url"] == "http://100.90.80.70:1234/v1"
        for path in remote["candidate_access_paths"]
    )

    offline = nodes["offline-node"]
    assert offline["online"] is False
    assert offline["admission_status"] == "candidate_only"

    checksum_path = output_path.with_suffix(output_path.suffix + ".sha256")
    expected = hashlib.sha256(output_path.read_bytes()).hexdigest()
    assert checksum_path.read_text(encoding="utf-8").startswith(expected)
