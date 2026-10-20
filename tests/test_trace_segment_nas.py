"""No live NAS writes: fake mount plumbing around real local encrypted bundles."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

# The existing fixture is >8 MiB and has 950 valid hash-linked records.
from test_trace_segment_plan import history as large_history  # noqa: F401

from assistx import trace_segment_nas as nas
from assistx.trace_execution_adapter import TraceDenied
from assistx.trace_segment_bundle import stage_bundle, verify_bundle


@pytest.fixture
def case(large_history, tmp_path, monkeypatch):  # noqa: F811 -- imported pytest fixture
    history = large_history
    source = tmp_path / "staged"
    source.mkdir(mode=0o700)
    witness = tmp_path / "witness"
    witness.mkdir(mode=0o700)
    nasroot = tmp_path / "fake-cifs-mount"
    nasroot.mkdir(mode=0o700)
    destination = nasroot / "trace-segments"
    destination.mkdir(mode=0o700)
    passphrase = tmp_path / "encryption.key"
    passphrase.write_text("fixture-test-only-passphrase-longer-than-32-characters")
    passphrase.chmod(0o600)
    key = b"V" * 48
    stage_bundle(
        history, root=source, node_id="xwing", passphrase=passphrase, signing_key=key, max_segment_bytes=1024 * 1024
    )
    calls = {"mount": 0, "alive": True}

    def fake_mount(root, *, expected_source, expected_target):
        calls["mount"] += 1
        if not calls["alive"]:
            raise TraceDenied("nas_mount_identity_mismatch")
        assert root == destination
        assert expected_source == "//fixture-mount/trace"
        assert expected_target == str(nasroot)

    monkeypatch.setattr(nas, "assert_mount", fake_mount)
    return {
        "source": source,
        "destination": destination,
        "witness": witness,
        "passphrase": passphrase,
        "key": key,
        "calls": calls,
        "nasroot": nasroot,
        "history": history,
    }


def publish(c):
    return nas.publish_bundle(
        source=c["source"],
        destination=c["destination"],
        witness_root=c["witness"],
        expected_mount_source="//fixture-mount/trace",
        expected_mount_target=str(c["nasroot"]),
        node_id="xwing",
        passphrase=c["passphrase"],
        signing_key=c["key"],
    )


def test_exact_encrypted_nas_publish_restores_and_witnesses(case):
    a = publish(case)
    assert a["ok"] and not a["reused"] and a["segments"] >= 9
    root = case["destination"] / "xwing" / hashlib.sha256(case["history"]).hexdigest()
    assert (
        verify_bundle(root, node_id="xwing", passphrase=case["passphrase"], signing_key=case["key"]) == case["history"]
    )
    rows = nas._witness_rows(case["witness"] / "published.jsonl", case["key"])
    assert len(rows) == 1 and rows[0]["journal_sha256"] == a["journal_sha256"]
    assert rows[0]["relative_archive"] == "xwing/" + a["journal_sha256"]
    b = publish(case)
    assert b["ok"] and b["reused"] and len(nas._witness_rows(case["witness"] / "published.jsonl", case["key"])) == 1
    assert case["calls"]["mount"] >= a["segments"] + 1


def test_mount_mismatch_does_not_create_archive_or_witness(case):
    case["calls"]["alive"] = False
    with pytest.raises(TraceDenied, match="nas_mount_identity_mismatch"):
        publish(case)
    assert not list(case["destination"].iterdir())
    assert not list(case["witness"].iterdir())


def test_ciphertext_tamper_denied_and_witness_is_not_advanced(case):
    publish(case)
    old_ledger = (case["witness"] / "published.jsonl").read_bytes()
    root = case["destination"] / "xwing" / hashlib.sha256(case["history"]).hexdigest()
    fragment = next(root.glob("segment-*.gpg"))
    with fragment.open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(TraceDenied, match="nas_existing_segment_conflict"):
        publish(case)
    assert (case["witness"] / "published.jsonl").read_bytes() == old_ledger


def test_partial_completed_segment_can_resume_without_rewrite(case):
    source = case["source"]
    root = case["destination"] / "xwing" / hashlib.sha256(case["history"]).hexdigest()
    root.mkdir(parents=True, mode=0o700)
    root.parent.chmod(0o700)
    root.chmod(0o700)
    name = next(source.glob("segment-*.gpg")).name
    shutil.copyfile(source / name, root / name)
    (root / name).chmod(0o600)
    before = hashlib.sha256((root / name).read_bytes()).hexdigest()
    result = publish(case)
    assert result["ok"] and not result["reused"]
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == before


def test_torn_witness_fails_closed(case):
    publish(case)
    witness = case["witness"] / "published.jsonl"
    witness.write_bytes(witness.read_bytes()[:-1])
    with pytest.raises(TraceDenied, match="nas_witness_torn"):
        publish(case)


def test_missing_witness_fails_if_existing_archive(case):
    publish(case)
    (case["witness"] / "published.jsonl").unlink()
    # Missing ledger and existing archive must not silently reset custody.
    with pytest.raises(TraceDenied, match="nas_witness_missing_with_archives_present"):
        publish(case)


def test_real_mount_probe_enforces_exact_configured_source(tmp_path, monkeypatch):
    nasroot = tmp_path / "mount"
    nasroot.mkdir()
    target = nasroot / "segments"
    target.mkdir()

    def response(source="cifs //fixture-mount/trace " + str(nasroot), returncode=0):
        return subprocess.CompletedProcess([], returncode, source + "\n", "")

    monkeypatch.setattr(nas.subprocess, "run", lambda *a, **kw: response())
    nas.assert_mount(target, expected_source="//fixture-mount/trace", expected_target=str(nasroot))
    monkeypatch.setattr(nas.subprocess, "run", lambda *a, **kw: response("ext4 /dev/loop1 " + str(nasroot)))
    with pytest.raises(TraceDenied, match="nas_mount_identity_mismatch"):
        nas.assert_mount(target, expected_source="//fixture-mount/trace", expected_target=str(nasroot))
    # A symlink mount-root is refused before calling findmnt.
    link = tmp_path / "symlink"
    link.symlink_to(target)
    with pytest.raises(TraceDenied, match="nas_root_missing_or_symlink"):
        nas.assert_mount(link, expected_source="//fixture-mount/trace", expected_target=str(nasroot))


def test_missing_mount_after_partial_write_does_not_sign_success(case):
    # Simulate the mount disappearing after the first encrypted object.
    count = {"n": 0}
    real_check = nas.assert_mount

    def fail_during_copy(root, *, expected_source, expected_target):
        count["n"] += 1
        if count["n"] > 5:
            raise TraceDenied("nas_mount_identity_mismatch")
        return real_check(root, expected_source=expected_source, expected_target=expected_target)

    from pytest import MonkeyPatch

    with MonkeyPatch.context() as patch:
        patch.setattr(nas, "assert_mount", fail_during_copy)
        with pytest.raises(TraceDenied, match="nas_mount_identity_mismatch"):
            publish(case)
    assert not (case["witness"] / "published.jsonl").exists()
    # Restored mount resumes byte-identical files without deleting any.
    assert publish(case)["ok"]
    assert len(nas._witness_rows(case["witness"] / "published.jsonl", case["key"])) == 1


def test_local_witness_cannot_reside_on_nas(case):
    bad = case["nasroot"] / "witness"
    bad.mkdir(mode=0o700)
    case["witness"] = bad
    with pytest.raises(TraceDenied, match="nas_source_or_witness_not_local"):
        publish(case)
    assert not list(case["destination"].iterdir())


def test_preexisting_remote_archive_directory_requires_private_mode(case):
    root = case["destination"] / "xwing"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(TraceDenied, match="unsafe_segment_directory"):
        publish(case)
    assert not (case["witness"] / "published.jsonl").exists()


def independent(case, journal_sha256):
    return nas.verify_published_bundle(
        destination=case["destination"],
        witness_root=case["witness"],
        expected_mount_source="//fixture-mount/trace",
        expected_mount_target=str(case["nasroot"]),
        node_id="xwing",
        journal_sha256=journal_sha256,
        passphrase=case["passphrase"],
        signing_key=case["key"],
    )


def test_independent_local_witness_and_nas_restore(case):
    published = publish(case)
    verified = independent(case, published["journal_sha256"])
    assert verified["ok"] and verified["records"] == 950
    assert verified["segments"] == published["segments"]
    assert verified["local_witness_verified"] and verified["nas_restore_verified"]
    assert verified["independent_worm_witness"] is False
    row = case["destination"] / "xwing" / published["journal_sha256"] / "index.json"
    row.write_bytes(row.read_bytes() + b" ")
    with pytest.raises(TraceDenied, match="nas_signed_index_hash_changed"):
        independent(case, published["journal_sha256"])


def test_independent_verifier_refuses_witness_reset(case):
    published = publish(case)
    (case["witness"] / "published.jsonl").unlink()
    with pytest.raises(TraceDenied, match="nas_signed_custody_record_missing_or_duplicate"):
        independent(case, published["journal_sha256"])


def test_insecure_existing_witness_lock_denied(case):
    lock = case["witness"] / ".publish.lock"
    lock.write_text("untrusted")
    lock.chmod(0o644)
    with pytest.raises(TraceDenied, match="nas_publish_lock_unsafe"):
        publish(case)
    assert not (case["witness"] / "published.jsonl").exists()
    assert not list(case["destination"].iterdir())


def test_real_mount_probe_accepts_systemd_autofs_overlay_only_with_cifs(tmp_path, monkeypatch):
    base = tmp_path / "mount"
    base.mkdir()
    destination = base / "segments"
    destination.mkdir()

    def stub(text):
        return subprocess.CompletedProcess([], 0, text, "")

    source = f"autofs systemd-1 {base}\ncifs //fixture-mount/trace {base}\n"
    monkeypatch.setattr(nas.subprocess, "run", lambda *a, **kw: stub(source))
    nas.assert_mount(destination, expected_source="//fixture-mount/trace", expected_target=str(base))
    monkeypatch.setattr(nas.subprocess, "run", lambda *a, **kw: stub(f"autofs systemd-1 {base}\n"))
    with pytest.raises(TraceDenied, match="nas_mount_identity_mismatch"):
        nas.assert_mount(destination, expected_source="//fixture-mount/trace", expected_target=str(base))
    monkeypatch.setattr(
        nas.subprocess,
        "run",
        lambda *a, **kw: stub(
            f"autofs systemd-1 {base}\ncifs //wrong/owner {base}\n",
        ),
    )
    with pytest.raises(TraceDenied, match="nas_mount_identity_mismatch"):
        nas.assert_mount(destination, expected_source="//fixture-mount/trace", expected_target=str(base))


def test_simultaneous_publishers_share_one_signed_custody_record(case):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(case), range(2)))
    assert all(r["ok"] for r in results)
    assert sorted(r["reused"] for r in results) == [False, True]
    assert len(nas._witness_rows(case["witness"] / "published.jsonl", case["key"])) == 1


def test_publisher_rejects_insecure_destination_before_writing(case):
    case["destination"].chmod(0o755)
    with pytest.raises(TraceDenied, match="nas_destination_permissions_unsafe"):
        publish(case)
    assert not list(case["destination"].iterdir())
    assert not list(case["witness"].iterdir())
