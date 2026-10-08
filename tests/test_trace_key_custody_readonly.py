"""Custody metadata is conservative and never exposes signer key bytes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/trace_key_custody_readonly.py"
SPEC = importlib.util.spec_from_file_location("trace_key_custody_readonly", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CUSTODY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CUSTODY)


def make_key(tmp_path, mode=0o600):
    folder = tmp_path / "keys"
    folder.mkdir(mode=0o700)
    key = folder / "private.pem"
    key.write_text("top-secret-do-not-leak", encoding="utf-8")
    key.chmod(mode)
    return key


def test_owner_private_signer_is_metadata_only(tmp_path, monkeypatch):
    key = make_key(tmp_path)
    def denied_read(self):
        raise AssertionError("signer key bytes must never be opened")
    monkeypatch.setattr(Path, "read_bytes", denied_read)
    result = CUSTODY.verify_metadata(str(key), private=True)
    assert result["metadata_acceptable"] is True
    assert result["private_content_read"] is False
    assert "top-secret" not in json.dumps(result)
    assert "private.pem" not in json.dumps(result)


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o777])
def test_world_or_group_access_denied_for_private_signer(tmp_path, mode):
    key = make_key(tmp_path, mode)
    result = CUSTODY.verify_metadata(str(key), private=True)
    assert result["metadata_acceptable"] is False


def test_symlink_signer_denied_without_dereference(tmp_path):
    key = make_key(tmp_path)
    alias = key.parent / "link"
    alias.symlink_to(key)
    result = CUSTODY.verify_metadata(str(alias), private=True)
    assert result["metadata_acceptable"] is False
    assert result["symlink"] is True


def test_public_key_pin_does_not_leak_key_data(tmp_path):
    key = make_key(tmp_path, 0o644)
    digest = hashlib.sha256(key.read_bytes()).hexdigest()
    good = CUSTODY.verify_metadata(str(key), private=False, expected_public_sha256=digest)
    bad = CUSTODY.verify_metadata(str(key), private=False, expected_public_sha256="f" * 64)
    assert good["metadata_acceptable"]
    assert good["public_pin_verified"]
    assert not bad["public_pin_verified"]
    assert "top-secret" not in json.dumps(good)


def test_no_config_never_passes_or_authorizes(tmp_path):
    result = CUSTODY.collect("issuer", "", None)
    assert result["key"]["state"] == "not_configured"
    assert result["private_key_content_read"] is False
    assert result["key_material_disclosed"] is False
    assert result["escrow_rotation_independently_verified"] is False
    assert result["issuer_verifier_pair_proven"] is False
    assert result["production_dispatch_authorized"] is False
    assert result["promotion_eligible"] is False


def test_unsafe_parent_directory_denied(tmp_path):
    key = make_key(tmp_path)
    key.parent.chmod(0o777)
    result = CUSTODY.verify_metadata(str(key), private=True)
    assert not result["metadata_acceptable"]


def test_missing_key_is_explicitly_unavailable(tmp_path):
    result = CUSTODY.verify_metadata(str(tmp_path / "absent"), private=True)
    assert result["state"] == "unavailable"
    assert result["metadata_acceptable"] is False
