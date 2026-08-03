from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_runtime_evidence",
    ROOT / "scripts" / "validate-runtime-evidence.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def write_evidence(root: Path, relative: str, body: bytes) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def manifest(root: Path) -> dict:
    capacity_sha = write_evidence(
        root,
        "artifacts/runtime/capacity.json",
        b'{"parallel_slots":1}',
    )
    lan_sha = write_evidence(
        root,
        "artifacts/runtime/lan.json",
        b'{"transport":"lan"}',
    )
    tailnet_sha = write_evidence(
        root,
        "artifacts/runtime/tailnet.json",
        b'{"transport":"tailscale"}',
    )
    model_sha = write_evidence(
        root,
        "artifacts/runtime/model.json",
        b'{"model_instance_id":"model-1"}',
    )
    return {
        "runtimes": [
            {
                "capacity": {
                    "evidence_ref": "artifacts/runtime/capacity.json",
                    "evidence_sha256": capacity_sha,
                },
                "access_paths": [
                    {
                        "evidence_ref": "artifacts/runtime/lan.json",
                        "evidence_sha256": lan_sha,
                    },
                    {
                        "evidence_ref": "artifacts/runtime/tailnet.json",
                        "evidence_sha256": tailnet_sha,
                    },
                ],
                "models": [
                    {
                        "evidence_ref": "artifacts/runtime/model.json",
                        "evidence_sha256": model_sha,
                    }
                ],
            }
        ]
    }


def test_runtime_evidence_verifies_every_artifact(tmp_path: Path) -> None:
    payload = manifest(tmp_path)

    failures, verified = module.validate(payload, root=tmp_path)

    assert failures == []
    assert len(verified) == 4
    assert {item["label"] for item in verified} == {
        "runtimes[0].capacity",
        "runtimes[0].access_paths[0]",
        "runtimes[0].access_paths[1]",
        "runtimes[0].models[0]",
    }


def test_runtime_evidence_rejects_mismatch_escape_and_missing_file(
    tmp_path: Path,
) -> None:
    payload = manifest(tmp_path)
    payload["runtimes"][0]["capacity"]["evidence_sha256"] = "0" * 64
    payload["runtimes"][0]["access_paths"][0]["evidence_ref"] = "../secret"
    payload["runtimes"][0]["models"][0]["evidence_ref"] = (
        "artifacts/runtime/missing.json"
    )

    failures, _ = module.validate(payload, root=tmp_path)

    assert any("mismatch" in failure for failure in failures)
    assert any("safe relative path" in failure for failure in failures)
    assert any("does not exist" in failure for failure in failures)
