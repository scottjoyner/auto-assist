"""Non-mutating, secret-free physical witness contract tests."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "trace_node_readonly_inventory.py"
SPEC = importlib.util.spec_from_file_location("trace_node_readonly_inventory", SOURCE)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


@pytest.mark.parametrize("raw,expected", [
    (None, "unobserved"),
    ("false", "false"),
    (" FALSE ", "false"),
    ("true", "true"),
    ("yes", "other"),
    ("secret-value", "other"),
])
def test_flags_only_allow_enumerated_nonsecret_states(raw, expected):
    assert PROBE.mask_flag(raw) == expected


def test_metadata_does_not_follow_secret_key_symlink(tmp_path):
    key = tmp_path / "signing-key"
    key.write_text("never disclose signing key data", encoding="utf-8")
    key.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(key)
    assert PROBE.metadata(link, require_private=True)["owner_private_regular"] is False
    info = PROBE.metadata(key, require_private=True)
    assert info["owner_private_regular"]
    assert "never disclose" not in json.dumps(info)
    key.chmod(0o644)
    assert not PROBE.metadata(key, require_private=True)["owner_private_regular"]


def test_readonly_journal_hash_does_not_modify_contents(tmp_path):
    root = tmp_path / "audit"
    root.mkdir(mode=0o700)
    path = root / "journal.jsonl"
    original = b'{"seq":1}\n{"seq":2}\n'
    path.write_bytes(original)
    path.chmod(0o600)
    before = path.stat().st_mtime_ns
    witness = PROBE.journal_metadata(str(root))
    assert witness["journal_sha256"] == hashlib.sha256(original).hexdigest()
    assert witness["journal_newline_count"] == 2
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == before


def test_unsafe_audit_root_denied_without_journal_read(tmp_path):
    root = tmp_path / "audit"
    root.mkdir(mode=0o755)
    (root / "journal.jsonl").write_bytes(b"sensitive-journal")
    result = PROBE.journal_metadata(str(root))
    assert result["journal"]["status"] == "unavailable_or_unsafe_root"
    assert "journal_sha256" not in result


def test_symlink_journal_not_opened(tmp_path):
    root = tmp_path / "audit"
    root.mkdir(mode=0o700)
    target = tmp_path / "target-secret"
    target.write_text("secret-content", encoding="utf-8")
    (root / "journal.jsonl").symlink_to(target)
    result = PROBE.journal_metadata(str(root))
    assert "journal_sha256" not in result
    assert result["journal"]["symlink"]


def test_process_record_never_serializes_secret_token_or_paths(tmp_path):
    fake_signer = tmp_path / "private-key.pem"
    fake_signer.write_bytes(b"very-secret-key-content")
    fake_signer.chmod(0o600)
    record = PROBE.safe_process_record(
        88, "fleet_worker", {
            "FLEET_NODE_AUTH_TOKEN": "dont-print-this-token",
            "FLEET_TRACE_ISSUER_ORIGIN": "https://sensitive-origin.example",
            "FLEET_TRACE_PROBE_ENABLED": "true",
            "FLEET_TRACE_REAL_EXECUTION_ENABLED": "false",
            "ASSISTX_TRACE_LEASE_SIGNING_KEY_FILE": str(fake_signer),
        }
    )
    serialized = json.dumps(record)
    assert "dont-print-this-token" not in serialized
    assert "sensitive-origin.example" not in serialized
    assert "private-key.pem" not in serialized
    assert "very-secret-key-content" not in serialized
    assert record["flag_states"]["FLEET_TRACE_PROBE_ENABLED"] == "true"
    assert record["flag_states"]["FLEET_TRACE_REAL_EXECUTION_ENABLED"] == "false"


def test_physical_observation_never_grants_authority(monkeypatch, tmp_path):
    monkeypatch.setattr(PROBE, "tailscale_identity", lambda: {
        "dns_name": "xwing.tailcb8954.ts.net.", "hostname": "xwing", "online": True,
    })
    monkeypatch.setattr(PROBE.platform, "system", lambda: "Linux")
    monkeypatch.setattr(PROBE, "scan_linux", lambda: [
        PROBE.safe_process_record(100, "fleet_worker", {
            "FLEET_TRACE_PROBE_ENABLED": "false",
            "FLEET_TRACE_REAL_EXECUTION_ENABLED": "false",
        }),
    ])
    monkeypatch.setattr(PROBE, "nas_policy", lambda: {"status": "mount_not_observed"})
    report = PROBE.collect("xwing", str(tmp_path / "audit"), str(tmp_path / "release"))
    assert report["tailnet_identity_matches_expected"]
    assert report["physical_authenticated_negative_admission"] == "not_tested"
    assert report["production_promotion_eligible"] is False
    assert report["dispatch_authorized"] is False
    assert report["claim_issued"] is False
    assert report["process_inventory_complete"] is False


def test_missing_macos_tailnet_identity_remains_unverified(monkeypatch, tmp_path):
    monkeypatch.setattr(PROBE, "tailscale_identity", lambda: {"status": "unavailable"})
    monkeypatch.setattr(PROBE.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(PROBE, "scan_macos", lambda: [])
    report = PROBE.collect("scotts-macbook-air", str(tmp_path), str(tmp_path))
    assert report["tailnet_identity_matches_expected"] is False
    assert report["production_promotion_eligible"] is False
