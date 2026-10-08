"""Offline adversarial fleet custody checks; never touch NAS or providers."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts/fleet_burn_custody.py"
spec = importlib.util.spec_from_file_location("fleet_custody", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def seeded(root, node="xwing", stamp="20261008T120000Z",
           content=b"sample", manifest_host=None):
    directory = root / node
    directory.mkdir(parents=True, exist_ok=True)
    name = "opencode_" + node + "_" + stamp + ".db.zst"
    archive = directory / name
    archive.write_bytes(content)
    (directory / "latest.txt").write_text(name + "\n")
    data = {"archive": name, "host": manifest_host or node,
            "archive_bytes": len(content),
            "archive_sha256": hashlib.sha256(content).hexdigest(),
            "stamp_utc": stamp, "ok": True, "quick_check": "ok",
            "source_stats": {"session": {"rows": 7}}}
    (directory / (name + ".manifest.json")).write_text(json.dumps(data))
    return directory, data


NOW = mod._utc_stamp("20261008T130000Z")


def test_custody_inventory_not_token_aggregation(tmp_path):
    seeded(tmp_path)
    result = mod.build_report(tmp_path, ["xwing"], now=NOW)
    node = result["nodes"][0]
    assert node["status"] == "MANIFEST_CHECKED"
    assert node["session_count_claim"] == 7
    assert not node["archive_hash_verified"]
    assert not node["usable_for_token_aggregation"]
    assert result["actual_fleet_token_count"] is None
    assert not result["token_aggregation_qualified"]


def test_verify_archive_hash_bounded(tmp_path):
    directory, manifest = seeded(tmp_path)
    assert mod.inspect_node(tmp_path, "xwing", now=NOW,
                            max_sha_bytes=10)["archive_hash_verified"]
    manifest["archive_sha256"] = "0" * 64
    name = next(directory.glob("*.manifest.json"))
    name.write_text(json.dumps(manifest))
    assert mod.inspect_node(tmp_path, "xwing", now=NOW,
                            max_sha_bytes=10)["reason"] == "archive_sha256_mismatch"


def test_quarantine_wrong_host_size_and_escape(tmp_path):
    directory, value = seeded(tmp_path, manifest_host="other-host")
    assert mod.inspect_node(tmp_path, "xwing", now=NOW)["reason"] == "host_identity_mismatch"
    value["host"] = "xwing"
    value["archive_bytes"] = 1000
    next(directory.glob("*.manifest.json")).write_text(json.dumps(value))
    assert mod.inspect_node(tmp_path, "xwing", now=NOW)["reason"] == "archive_size_mismatch"
    (directory / "latest.txt").write_text("../../secrets")
    assert mod.inspect_node(tmp_path, "xwing", now=NOW)["reason"] == "invalid_latest_filename"


def test_stale_and_missing_reported_without_fake_zeros(tmp_path):
    seeded(tmp_path, stamp="20260901T000000Z")
    result = mod.build_report(tmp_path, ["xwing", "missing"], now=NOW)
    assert result["stale_nodes"] == 1
    assert result["quarantined_nodes"] == 1
    assert result["expected_nodes"] == 2
    assert result["actual_fleet_token_count"] is None
    assert not result["archive_integrity_verified_for_all"]


def test_clone_archives_quarantined_from_aggregation(tmp_path):
    seeded(tmp_path, "xwing")
    seeded(tmp_path, "deathstar-XPS-8920")
    out = mod.build_report(tmp_path, ["xwing", "deathstar-XPS-8920"], now=NOW)
    assert out["digest_reuse_groups"] == [["deathstar-XPS-8920", "xwing"]]
    assert not out["token_aggregation_qualified"]
    assert out["actual_fleet_token_count"] is None


def test_invalid_roster_and_symlink(tmp_path):
    seeded(tmp_path)
    assert mod.inspect_node(tmp_path, "../private", now=NOW)["reason"] == "invalid_node_identity"
    import pytest
    with pytest.raises(ValueError, match="duplicate"):
        mod.build_report(tmp_path, ["xwing", "xwing"], now=NOW)
    directory=tmp_path/"xwing"
    (directory/"latest.txt").unlink()
    (directory/"latest.txt").symlink_to(directory/"opencode_xwing_20261008T120000Z.db.zst")
    assert mod.inspect_node(tmp_path, "xwing", now=NOW)["status"] == "QUARANTINED"
