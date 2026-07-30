#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import time
from typing import Any


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=capture,
    )


def read_image_ids(path: pathlib.Path) -> list[str]:
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and value not in values:
            values.append(value)
    if not values:
        raise ValueError(f"no image IDs found in {path}")
    return values


def inspect_images(docker: str, image_ids: list[str]) -> list[dict[str, Any]]:
    payload = json.loads(run([docker, "image", "inspect", *image_ids]).stdout)
    result = []
    for item in payload:
        result.append(
            {
                "id": item.get("Id"),
                "repo_tags": item.get("RepoTags") or [],
                "repo_digests": item.get("RepoDigests") or [],
                "created": item.get("Created"),
                "architecture": item.get("Architecture"),
                "os": item.get("Os"),
                "size": item.get("Size"),
            }
        )
    return result


def capture(args: argparse.Namespace) -> int:
    output_dir: pathlib.Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    image_ids = read_image_ids(args.image_id_file)
    images = inspect_images(args.docker, image_ids)
    bundle_path = output_dir / "reconciliation-images.tar"
    manifest_path = output_dir / "reconciliation-images.manifest.json"
    checksum_path = output_dir / "reconciliation-images.tar.sha256"

    run([args.docker, "save", "--output", str(bundle_path), *image_ids], capture=False)
    bundle_sha = sha256_file(bundle_path)
    manifest = {
        "schema_version": 1,
        "captured_at_ts": int(time.time() * 1000),
        "bundle": bundle_path.name,
        "bundle_sha256": bundle_sha,
        "image_count": len(images),
        "images": images,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksum_path.write_text(f"{bundle_sha}  {bundle_path.name}\n", encoding="utf-8")
    print(f"IMAGE_BUNDLE_CAPTURE: PASS ({len(images)} images)")
    print(manifest_path)
    print(checksum_path)
    return 0


def verify(args: argparse.Namespace) -> int:
    manifest_path: pathlib.Path = args.manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_path = manifest_path.parent / str(manifest.get("bundle") or "reconciliation-images.tar")
    expected_sha = str(manifest.get("bundle_sha256") or "")
    actual_sha = sha256_file(bundle_path)
    if not expected_sha or actual_sha != expected_sha:
        print(
            f"IMAGE_BUNDLE_VERIFY: BLOCKED checksum mismatch expected={expected_sha} actual={actual_sha}",
            file=sys.stderr,
        )
        return 1

    load_output = "skipped"
    if not args.no_load:
        completed = run([args.docker, "load", "--input", str(bundle_path)])
        load_output = completed.stdout.strip()[-4000:]

    missing = []
    for image in manifest.get("images") or []:
        image_id = str(image.get("id") or "")
        if not image_id:
            missing.append("<missing-id-in-manifest>")
            continue
        try:
            run([args.docker, "image", "inspect", image_id])
        except subprocess.CalledProcessError:
            missing.append(image_id)

    evidence = {
        "schema_version": 1,
        "verified_at_ts": int(time.time() * 1000),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "bundle": str(bundle_path),
        "bundle_sha256": actual_sha,
        "image_count": len(manifest.get("images") or []),
        "missing_images": missing,
        "docker_load_executed": not args.no_load,
        "docker_load_output": load_output,
        "network_required": False,
        "status": "pass" if not missing else "blocked",
    }
    evidence_path = manifest_path.parent / "reconciliation-images.restore-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if missing:
        print(f"IMAGE_BUNDLE_VERIFY: BLOCKED missing image IDs: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"IMAGE_BUNDLE_VERIFY: PASS ({evidence['image_count']} images loadable without pulls)")
    print(evidence_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture and verify a local Docker image rollback bundle.")
    parser.add_argument("--docker", default="docker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--image-id-file", type=pathlib.Path, required=True)
    capture_parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    capture_parser.set_defaults(func=capture)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--manifest", type=pathlib.Path, required=True)
    verify_parser.add_argument("--no-load", action="store_true")
    verify_parser.set_defaults(func=verify)

    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"IMAGE_BUNDLE: BLOCKED {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
