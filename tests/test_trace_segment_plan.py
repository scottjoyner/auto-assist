"""Read-only >8 MiB immutable-segment preservation acceptance."""

from __future__ import annotations

import hashlib
import json

import pytest

from assistx.trace_execution_adapter import TraceDenied
from assistx.trace_segment_plan import canonical, plan_segments, verify_segments


@pytest.fixture(scope="module")
def history():
    prev = "0" * 64
    parts = []
    # Deliberately larger than current 8 MiB monolithic custody ceiling.
    for n in range(1, 951):
        item = {
            "schema": "assistx.trace-execution.v1",
            "seq": n,
            "prev_hash": prev,
            "node_id": "xwing",
            "event": "prepared" if n % 2 else "completed",
            "task_id": f"fixture-{n // 2}",
            "evidence_padding": "X" * 9300,
        }
        prev = hashlib.sha256(canonical(item)).hexdigest()
        item["entry_hash"] = prev
        parts.append(canonical(item) + b"\n")
    raw = b"".join(parts)
    assert len(raw) > 8 * 1024 * 1024
    return raw


def test_immutable_segments_reconstruct_byte_exact_large_history(history):
    manifest, segments = plan_segments(
        history,
        node_id="xwing",
        signing_key=b"a" * 48,
        max_segment_bytes=320_000,
    )
    assert len(segments) > 20
    assert all(len(x) <= 320_000 for x in segments)
    assert manifest["records"] == 950
    assert b"".join(segments) == history
    assert (
        verify_segments(
            manifest,
            segments,
            node_id="xwing",
            signing_key=b"a" * 48,
        )
        == history
    )


@pytest.mark.parametrize("damage", ["remove", "swap", "tamper", "append", "truncate"])
def test_missing_reordered_or_modified_segment_is_denied(history, damage):
    manifest, original = plan_segments(history, node_id="xwing", signing_key=b"b" * 48)
    fragments = list(original)
    if damage == "remove":
        fragments.pop(2)
    if damage == "swap":
        fragments[0], fragments[1] = fragments[1], fragments[0]
    if damage == "tamper":
        fragments[1] = b"X" + fragments[1][1:]
    if damage == "append":
        fragments.append(b"forged")
    if damage == "truncate":
        fragments[-1] = fragments[-1][:-1]
    with pytest.raises(TraceDenied):
        verify_segments(
            manifest,
            fragments,
            node_id="xwing",
            signing_key=b"b" * 48,
        )


def test_signed_manifest_authentication_and_node_pinning(history):
    manifest, segments = plan_segments(history, node_id="xwing", signing_key=b"c" * 48)
    with pytest.raises(TraceDenied, match="segment_signature_mismatch"):
        verify_segments(manifest, segments, node_id="xwing", signing_key=b"d" * 48)
    with pytest.raises(TraceDenied, match="segment_manifest_identity_mismatch"):
        verify_segments(manifest, segments, node_id="scotts-macbook-air", signing_key=b"c" * 48)
    fake = {**manifest, "records": 951}
    with pytest.raises(TraceDenied, match="segment_signature_mismatch"):
        verify_segments(fake, segments, node_id="xwing", signing_key=b"c" * 48)


@pytest.mark.parametrize("size", [0, 1023, 8 * 1024 * 1024 + 1, True, 1.2])
def test_invalid_segment_limit_denied(history, size):
    with pytest.raises(TraceDenied, match="invalid_segment_size"):
        plan_segments(history, node_id="xwing", signing_key=b"d" * 48, max_segment_bytes=size)


def test_large_record_rejected_without_rewriting_source(history):
    before = hashlib.sha256(history).hexdigest()
    with pytest.raises(TraceDenied, match="trace_record_exceeds_segment_size"):
        plan_segments(history, node_id="xwing", signing_key=b"e" * 48, max_segment_bytes=2048)
    assert hashlib.sha256(history).hexdigest() == before


def test_wrong_node_rejected_before_manifest_generation(history):
    with pytest.raises(TraceDenied, match="segment_node_identity_mismatch"):
        plan_segments(history, node_id="scotts-macbook-air", signing_key=b"f" * 48)


@pytest.mark.skipif(not __import__("shutil").which("gpg"), reason="gpg unavailable")
def test_encrypted_segment_bundle_over_eight_megabytes_is_idempotent(history, tmp_path):
    from assistx.trace_segment_bundle import stage_bundle, verify_bundle

    root = tmp_path / "private-bundle"
    root.mkdir(mode=0o700)
    secret = tmp_path / "encryption.passphrase"
    secret.write_text("fixture-only-passphrase-of-sufficient-length-for-gpg")
    secret.chmod(0o600)
    key = b"z" * 48
    first = stage_bundle(
        history,
        root=root,
        node_id="xwing",
        passphrase=secret,
        signing_key=key,
        max_segment_bytes=1024 * 1024,
    )
    assert first["ok"] and not first["reused"] and first["segments"] > 8
    assert verify_bundle(root, node_id="xwing", passphrase=secret, signing_key=key) == history
    second = stage_bundle(
        history,
        root=root,
        node_id="xwing",
        passphrase=secret,
        signing_key=key,
        max_segment_bytes=1024 * 1024,
    )
    assert second["ok"] and second["reused"]
    assert len(list(root.glob("segment-*.jsonl.gpg"))) == first["segments"]
    assert not (root / "journal.jsonl").exists()
    with pytest.raises(TraceDenied, match="segment_index_node_mismatch"):
        verify_bundle(root, node_id="scotts-macbook-air", passphrase=secret, signing_key=key)
    with pytest.raises(TraceDenied, match="segment_index_signature_invalid"):
        verify_bundle(root, node_id="xwing", passphrase=secret, signing_key=b"y" * 48)
    fragment = next(root.glob("segment-*.jsonl.gpg"))
    with fragment.open("ab") as file:
        file.write(b"tampered")
    with pytest.raises(TraceDenied, match="segment_ciphertext_hash_invalid"):
        verify_bundle(root, node_id="xwing", passphrase=secret, signing_key=key)


@pytest.mark.skipif(not __import__("shutil").which("gpg"), reason="gpg unavailable")
def test_two_concurrent_stagers_cannot_replace_prior_segments(history, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from assistx.trace_segment_bundle import stage_bundle, verify_bundle

    root = tmp_path / "bundles"
    root.mkdir(mode=0o700)
    secret = tmp_path / "secret"
    secret.write_text("another-test-only-32-character-passphrase-text")
    secret.chmod(0o600)

    def run(_):
        return stage_bundle(
            history,
            root=root,
            node_id="xwing",
            passphrase=secret,
            signing_key=b"k" * 48,
            max_segment_bytes=1024 * 1024,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(run, range(2)))
    assert sorted([a["reused"], b["reused"]]) == [False, True]
    assert verify_bundle(root, node_id="xwing", passphrase=secret, signing_key=b"k" * 48) == history


@pytest.mark.skipif(not __import__("shutil").which("gpg"), reason="gpg unavailable")
def test_interrupted_first_segment_is_verified_and_reused(history, tmp_path):
    from assistx.trace_segment_bundle import _filename, _gpg, stage_bundle, verify_bundle

    root = tmp_path / "segments"
    root.mkdir(mode=0o700)
    secret = tmp_path / "secret"
    secret.write_text("yet-another-test-only-32-character-passphrase-text")
    secret.chmod(0o600)
    manifest, pieces = plan_segments(
        history,
        node_id="xwing",
        signing_key=b"p" * 48,
        max_segment_bytes=1024 * 1024,
    )
    first = root / _filename(1, manifest["segments"][0]["sha256"])
    _gpg(pieces[0], first, secret, decrypt=False)
    first.chmod(0o600)
    initial_digest = hashlib.sha256(first.read_bytes()).hexdigest()
    result = stage_bundle(
        history,
        root=root,
        node_id="xwing",
        passphrase=secret,
        signing_key=b"p" * 48,
        max_segment_bytes=1024 * 1024,
    )
    assert result["ok"] and not result["reused"]
    assert hashlib.sha256(first.read_bytes()).hexdigest() == initial_digest
    assert verify_bundle(root, node_id="xwing", passphrase=secret, signing_key=b"p" * 48) == history


def test_insecure_passphrase_denied_before_stage(history, tmp_path):
    from assistx.trace_segment_bundle import stage_bundle

    root = tmp_path / "bundle"
    root.mkdir(mode=0o700)
    key = tmp_path / "unsafe-key"
    key.write_text("fixture-only-but-insecure-readable-key-32-characters")
    key.chmod(0o644)
    with pytest.raises(TraceDenied, match="segment_file_unsafe"):
        stage_bundle(history, root=root, node_id="xwing", passphrase=key, signing_key=b"q" * 48)
    assert not list(root.iterdir())
