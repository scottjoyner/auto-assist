"""Private snapshot writer and replay protection tests (offline only)."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fleet_endpoint_snapshot.py"
spec = importlib.util.spec_from_file_location("fleet_endpoint_snapshot_for_test", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_private_snapshot_receipt_and_lineage(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    data = {
        "schema": "fleet-endpoint-harness-report/v1",
        "mode": "offline_read_only", "production_admission_grants": 0,
        "endpoints": [{"classification": "not_qualified"}],
    }
    result = module.snapshot(data, root / "reports", private_root=root,
                             source_hashes={"inventory": "a" * 64})
    p, r = Path(result["report"]), Path(result["receipt"])
    assert p.is_file() and r.is_file()
    assert p.stat().st_mode & 0o777 == 0o600
    assert r.stat().st_mode & 0o777 == 0o600
    assert p.parent.stat().st_mode & 0o777 == 0o700
    metadata = json.loads(r.read_text())
    assert hashlib.sha256(p.read_bytes()).hexdigest() == metadata["report_sha256"]
    assert metadata["routing_authorized"] is False
    assert metadata["independently_archived"] is False
    assert metadata["sources_sha256"]["inventory"] == "a" * 64
    assert json.loads(p.read_text()) == data


def test_repeated_runs_never_overwrite_previous_snapshots(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    a = module.snapshot({"k": 1}, root / "reports", private_root=root, source_hashes={})
    b = module.snapshot({"k": 2}, root / "reports", private_root=root, source_hashes={})
    assert a["report"] != b["report"]
    assert json.loads(Path(a["report"]).read_text())["k"] == 1
    assert json.loads(Path(b["report"]).read_text())["k"] == 2


def test_snapshot_outside_private_root_refused(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    with pytest.raises(ValueError):
        module.snapshot({}, tmp_path / "not-private", private_root=root, source_hashes={})
    assert not (tmp_path / "not-private").exists()


def test_snapshot_symlink_egress_refused(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    (root / "sym").symlink_to(escaped, target_is_directory=True)
    with pytest.raises(ValueError):
        module.snapshot({}, root / "sym", private_root=root, source_hashes={})
    assert not list(escaped.iterdir())


def test_fingerprint_canonicalization():
    assert module.digest_json({"a": 1, "b": 2}) == module.digest_json({"b": 2, "a": 1})


def test_local_receipt_carries_no_secret_sources(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    evidence = {"api_key": "private-secret", "model": "m"}
    result = module.snapshot(
        {"schema": "report/v1", "production_admission_grants": 0},
        root / "reports", private_root=root,
        source_hashes={"source": module.digest_json(evidence)},
    )
    assert "private-secret" not in Path(result["receipt"]).read_text()
    assert "private-secret" not in Path(result["report"]).read_text()
