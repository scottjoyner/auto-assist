"""No-network verification of actual bytes and sanitized convergence view v2."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "convergence_readback", ROOT / "scripts/validate-convergence-readback.py"
)
assert SPEC and SPEC.loader
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)
SHA = "a" * 64
REVISION = "synthetic-revision"


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def fixture(tmp_path, *, persist=True):
    artifact = {
        "schema_version": 1,
        "projection_view_schema_version": 2,
        "verified_at_ts": 1780000000000,
        "status": "pass",
        "failures": [],
        "assistx_projection": {
            "generation": 7,
            "revision_sha256": hashlib.sha256(REVISION.encode()).hexdigest(),
            "checksum": SHA,
            "expires_at_ms": 2000000000000,
            "provider_count": 1,
        },
        "router_projection": {
            "generation": 7,
            "revision_sha256": hashlib.sha256(REVISION.encode()).hexdigest(),
            "checksum": SHA,
            "admission": [{"parallel_slots": 2}],
            "admission_count": 1,
        },
        "retired_generations": [],
    }
    artifact["evidence_sha256"] = canonical_digest(artifact)
    path = tmp_path / "artifacts" / "projection.json"
    path.parent.mkdir(exist_ok=True)
    if persist:
        path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    manifest = {
        "runtime_projection": {
            "convergence_status": "pass",
            "generation": 7,
            "revision": REVISION,
            "projection_checksum": SHA,
            "convergence_evidence_path": "artifacts/projection.json",
            "convergence_evidence_sha256": (
                hashlib.sha256(path.read_bytes()).hexdigest() if persist else SHA
            ),
        }
    }
    return manifest, artifact, path


def resave(manifest, artifact, path, *, signed=True):
    if signed:
        artifact["evidence_sha256"] = canonical_digest(
            {k: v for k, v in artifact.items() if k != "evidence_sha256"}
        )
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    manifest["runtime_projection"]["convergence_evidence_sha256"] = (
        hashlib.sha256(path.read_bytes()).hexdigest()
    )


def test_genuine_v2_artifact_and_manifest_match_pass(tmp_path):
    manifest, artifact, path = fixture(tmp_path)
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == []
    assert artifact["evidence_sha256"] != manifest["runtime_projection"]["convergence_evidence_sha256"]


@pytest.mark.parametrize("mutate,code", [
    (lambda m,a,p: m["runtime_projection"].update(convergence_evidence_sha256="b"*64),
     "artifact_file_digest_mismatch"),
    (lambda m,a,p: m["runtime_projection"].update(generation=8),
     "artifact_manifest_generation_mismatch"),
    (lambda m,a,p: m["runtime_projection"].update(revision="bad-revision"),
     "artifact_manifest_revision_mismatch"),
    (lambda m,a,p: m["runtime_projection"].update(projection_checksum="b"*64),
     "artifact_manifest_checksum_mismatch"),
    (lambda m,a,p: m["runtime_projection"].update(convergence_evidence_path="../outside"),
     "artifact_path_outside_artifacts"),
    (lambda m,a,p: m["runtime_projection"].update(convergence_evidence_path="/etc/passwd"),
     "artifact_path_outside_artifacts"),
    (lambda m,a,p: m["runtime_projection"].update(convergence_status="not_run"),
     "manifest_convergence_not_pass"),
])
def test_manifest_tampering_denied(tmp_path, mutate, code):
    manifest, artifact, path = fixture(tmp_path)
    mutate(manifest, artifact, path)
    assert code in reader.verify(manifest, root=tmp_path, now_ms=1800000000000)


@pytest.mark.parametrize("mutate,code", [
    (lambda a: a.update(projection_view_schema_version=1), "artifact_view_version_unrecognized"),
    (lambda a: a.update(status="blocked"), "artifact_convergence_not_pass"),
    (lambda a: a.update(failures=["untrusted text"]), "artifact_convergence_not_pass"),
    (lambda a: a["router_projection"].update(secret="sensitive"), "artifact_projection_identity_invalid"),
    (lambda a: a["router_projection"]["admission"][0].update(runtime_instance_id="sensitive"),
     "artifact_admission_invalid"),
    (lambda a: a["router_projection"].update(admission_count=10), "artifact_admission_invalid"),
    (lambda a: a["assistx_projection"].update(expires_at_ms=1), "artifact_projection_expired"),
    (lambda a: a["assistx_projection"].update(generation=8), "artifact_cross_projection_mismatch"),
    (lambda a: a["router_projection"].update(revision_sha256="b"*64), "artifact_revision_mismatch"),
    (lambda a: a.update(retired_generations=[{"generation": "bad", "checksum": SHA,
         "revision_sha256": SHA}]), "artifact_retired_generations_invalid"),
])
def test_rehashed_malicious_payload_still_denied(tmp_path, mutate, code):
    manifest, artifact, path = fixture(tmp_path)
    mutate(artifact)
    resave(manifest, artifact, path)
    assert code in reader.verify(manifest, root=tmp_path, now_ms=1800000000000)


def test_embedded_digest_mismatch_denied_even_if_file_digest_matches(tmp_path):
    manifest, artifact, path = fixture(tmp_path)
    artifact["router_projection"]["admission"][0]["parallel_slots"] = 6
    resave(manifest, artifact, path, signed=False)
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == [
        "artifact_embedded_digest_mismatch"
    ]


def test_missing_and_expired_files_denied(tmp_path):
    manifest, artifact, path = fixture(tmp_path)
    path.unlink()
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == ["artifact_missing"]
    manifest, artifact, path = fixture(tmp_path)
    assert reader.verify(manifest, root=tmp_path, now_ms=2100000000000) == ["artifact_projection_expired"]


def test_symlink_outside_root_denied(tmp_path):
    manifest, artifact, path = fixture(tmp_path)
    external = tmp_path.parent / (tmp_path.name + "-outside")
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == [
        "artifact_symlink_or_not_regular"
    ]


def test_duplicate_keys_and_nan_denied(tmp_path):
    manifest, artifact, path = fixture(tmp_path)
    path.write_text('{"schema_version":1,"schema_version":1}')
    manifest["runtime_projection"]["convergence_evidence_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert reader.verify(manifest, root=tmp_path) == ["artifact_json_invalid"]
    path.write_text('{"schema_version":NaN}')
    manifest["runtime_projection"]["convergence_evidence_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert reader.verify(manifest, root=tmp_path) == ["artifact_json_invalid"]


def test_real_v2_producer_to_readback_contract_when_available(
    monkeypatch, capsys, tmp_path,
):
    """Use the actual sanitized writer in combined trees; old main cannot emit v2."""
    import importlib.util
    import sys

    source = ROOT / "scripts" / "verify-runtime-projection.py"
    if "projection_view_schema_version" not in source.read_text():
        pytest.skip("sanitized v2 producer is in separate unmerged PR #371")

    spec = importlib.util.spec_from_file_location("v2_convergence_producer", source)
    assert spec and spec.loader
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)

    manifest, _, path = fixture(tmp_path, persist=False)
    source_json = {
        "source": "assistx",
        "generation": 7,
        "revision": REVISION,
        "checksum": SHA,
        "expires_at_ms": 2000000000000,
        "providers": [{"name": "synthetic"}],
    }
    router_json = {
        "configured": True,
        "last_error": None,
        "current": {
            "generation": 7,
            "revision": REVISION,
            "checksum": SHA,
            "admission": [{"parallel_slots": 2, "runtime_instance_id": "synthetic"}],
        },
        "retired_generations": [],
    }
    calls = []

    def fake_request(url, headers):
        calls.append(url)
        return source_json if len(calls) == 1 else router_json

    monkeypatch.setattr(producer, "_get_json", fake_request)
    for key, value in {
        "BASIC_AUTH_USER": "synthetic",
        "BASIC_AUTH_PASS": "synthetic",
        "AUTO_ROUTER_ADMIN_TOKEN": "synthetic",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        sys, "argv", [
            "verify-runtime-projection.py", "--assistx-url", "http://unused.invalid",
            "--router-url", "http://unused.invalid", "--output", str(path),
        ],
    )
    assert producer.main() == 0
    assert len(calls) == 2
    manifest["runtime_projection"]["convergence_evidence_sha256"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == []
    assert "RUNTIME_PROJECTION_GATE: PASS" in capsys.readouterr().out

    # Same real producer output must fail on post-verification tampering.
    path.write_bytes(path.read_bytes() + b" ")
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == [
        "artifact_file_digest_mismatch"
    ]


def test_cli_reads_manifest_and_prints_bounded_result(monkeypatch, capsys, tmp_path):
    import sys

    import yaml

    manifest, _, _ = fixture(tmp_path)
    manifest_file = tmp_path / "final-cutover.yaml"
    manifest_file.write_text(yaml.safe_dump(manifest))
    monkeypatch.setattr(
        sys, "argv", [
            "validate-convergence-readback.py", str(manifest_file),
            "--root", str(tmp_path),
        ],
    )
    # Exercise CLI using actual wall-clock, whose date remains before fixture expiry.
    assert reader.main() == 0
    result = capsys.readouterr()
    assert result.out.strip() == "CONVERGENCE_READBACK: VERIFIED_ARTIFACT_CONSISTENCY_ONLY"
    assert result.err == ""

    manifest["runtime_projection"]["convergence_evidence_sha256"] = SHA
    manifest_file.write_text(yaml.safe_dump(manifest))
    assert reader.main() == 1
    result = capsys.readouterr()
    assert "BLOCKED artifact_file_digest_mismatch" in result.err
    assert "synthetic" not in result.err

def test_symlink_inside_artifacts_is_rejected_not_followed(tmp_path):
    manifest, artifact, path = fixture(tmp_path)
    target = tmp_path / "artifacts" / "source.json"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == [
        "artifact_symlink_or_not_regular"
    ]


def test_symlink_directory_inside_artifacts_is_rejected(tmp_path):
    manifest, artifact, path = fixture(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    other.joinpath("projection.json").write_bytes(path.read_bytes())
    path.unlink()
    path.parent.rmdir()
    path.parent.symlink_to(other, target_is_directory=True)
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == [
        "artifact_symlink_or_not_regular"
    ]


def test_path_swapped_at_final_open_never_follows_outside_root(monkeypatch, tmp_path):
    import os

    manifest, artifact, path = fixture(tmp_path)
    external = tmp_path.parent / (tmp_path.name + "-external-valid-artifact.json")
    external.write_bytes(path.read_bytes())  # Exact hash match defeats digest-only tests.
    original_open = os.open
    intercepted = []

    def swap_when_opening_leaf(file, flags, *args, **kwargs):
        if file == "projection.json" and not intercepted:
            intercepted.append(flags)
            path.unlink()
            path.symlink_to(external)
        return original_open(file, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_when_opening_leaf)
    result = reader.verify(manifest, root=tmp_path, now_ms=1800000000000)
    assert intercepted, "the verifier must open the leaf using the pinned directory fd"
    assert intercepted[0] & os.O_NOFOLLOW
    assert result == ["artifact_symlink_or_not_regular"]


def test_hardlink_or_special_file_is_rejected(tmp_path):
    import os

    manifest, artifact, path = fixture(tmp_path)
    sibling = path.with_name("also-projection.json")
    os.link(path, sibling)
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == [
        "artifact_symlink_or_not_regular"
    ]

def test_oversized_file_rejected_before_full_read(tmp_path):
    manifest, _, path = fixture(tmp_path)
    path.write_bytes(b"X" * (reader._MAX_BYTES + 1))
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == [
        "artifact_excessive_size"
    ]


def test_nonregular_fifo_rejected_without_blocking(tmp_path):
    import os

    manifest, _, path = fixture(tmp_path)
    path.unlink()
    os.mkfifo(path)
    assert reader.verify(manifest, root=tmp_path, now_ms=1800000000000) == [
        "artifact_symlink_or_not_regular"
    ]


def test_content_mutation_detected_during_open_descriptor_read(monkeypatch, tmp_path):
    import os

    manifest, _, path = fixture(tmp_path)
    old_read = os.read
    mutated = []

    def read_then_mutate(fd, length):
        chunk = old_read(fd, length)
        if chunk and not mutated:
            mutated.append(True)
            with path.open("ab") as out:
                out.write(b" ")
        return chunk

    monkeypatch.setattr(os, "read", read_then_mutate)
    result = reader.verify(manifest, root=tmp_path, now_ms=1800000000000)
    assert mutated
    assert result == ["artifact_changed_during_read"]
