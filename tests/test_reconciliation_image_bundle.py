from __future__ import annotations

import hashlib
import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "reconciliation_image_bundle",
    ROOT / "scripts" / "reconciliation-image-bundle.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_sha256_file_matches_standard_library(tmp_path) -> None:
    path = tmp_path / "bundle.tar"
    path.write_bytes(b"offline-image-bundle")

    assert module.sha256_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_read_image_ids_deduplicates_and_rejects_empty(tmp_path) -> None:
    path = tmp_path / "images.txt"
    path.write_text("sha256:a\nsha256:b\nsha256:a\n\n", encoding="utf-8")

    assert module.read_image_ids(path) == ["sha256:a", "sha256:b"]

    empty = tmp_path / "empty.txt"
    empty.write_text("\n", encoding="utf-8")
    try:
        module.read_image_ids(empty)
    except ValueError as exc:
        assert "no image IDs" in str(exc)
    else:
        raise AssertionError("empty image file must fail closed")
