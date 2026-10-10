"""Guard-rail tests for physical Neo4j receipt experiment; no Docker calls."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "physical" / "fleet_receipt_disposable_neo4j.py"
_SPEC = importlib.util.spec_from_file_location("disposable_receipt_lab_guard", _SCRIPT)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def example_meta() -> dict:
    return {
        "Name": "/assistx-receipt-lab-20261010",
        "State": {"Running": True},
        "Config": {"Image": "neo4j:5.23.0"},
        "HostConfig": {"Memory": 1536 * 1024**2, "NanoCpus": 750000000, "Binds": None},
        "Mounts": [{"Type": "volume", "Destination": "/data"}],
        "NetworkSettings": {"Ports": {"7687/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32768"}]}},
    }


def test_guard_accepts_exact_disposable_profile():
    _MODULE.validate_disposable_container(example_meta(), 32768)


@pytest.mark.parametrize(
    "mutation",
    [
        ("Name", "/neo4j"),
        ("State", {"Running": False}),
        ("Config", {"Image": "neo4j:latest"}),
        ("HostConfig", {"Memory": 0, "NanoCpus": 750000000, "Binds": None}),
        ("HostConfig", {"Memory": 1536 * 1024**2, "NanoCpus": 0, "Binds": None}),
        ("HostConfig", {"Memory": 1536 * 1024**2, "NanoCpus": 750000000, "Binds": ["/nas:/data"]}),
        ("Mounts", [{"Type": "bind", "Source": "/nas", "Destination": "/data"}]),
        ("NetworkSettings", {"Ports": {"7687/tcp": [{"HostIp": "0.0.0.0", "HostPort": "32768"}]}}),
        ("NetworkSettings", {"Ports": {"7687/tcp": [{"HostIp": "127.0.0.1", "HostPort": "7687"}]}}),
    ],
)
def test_guard_rejects_wrong_or_unbounded_target(mutation):
    field, value = mutation
    bad = example_meta()
    bad[field] = value
    with pytest.raises(RuntimeError):
        _MODULE.validate_disposable_container(bad, 32768)
