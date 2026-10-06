#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


PRIVATE_MODE = 0o600
PUBLIC_MODE = 0o644


def _write_exclusive(path: Path, data: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    path.chmod(mode)


def _keypair(directory: Path, stem: str) -> tuple[Path, Path, str]:
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path = directory / f"{stem}-private.pem"
    public_path = directory / f"{stem}-public.pem"
    _write_exclusive(private_path, private_bytes, PRIVATE_MODE)
    _write_exclusive(public_path, public_bytes, PUBLIC_MODE)
    fingerprint = hashlib.sha256(public_bytes).hexdigest()
    return private_path.resolve(), public_path.resolve(), fingerprint


def _token() -> str:
    return secrets.token_urlsafe(48)


def generate(output_dir: Path) -> dict[str, Path | dict]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)

    expected = [
        output_dir / "executor-private.pem",
        output_dir / "executor-public.pem",
        output_dir / "runtime-projection-private.pem",
        output_dir / "runtime-projection-public.pem",
        output_dir / ".env.reconciliation.generated",
        output_dir / "reconciliation-secrets-manifest.json",
    ]
    existing = [str(path) for path in expected if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite existing reconciliation credentials: "
            + ", ".join(existing)
        )

    executor_private, executor_public, executor_fingerprint = _keypair(
        output_dir,
        "executor",
    )
    projection_private, projection_public, projection_fingerprint = _keypair(
        output_dir,
        "runtime-projection",
    )

    values = {
        "ASSISTX_EXECUTOR_PRIVATE_KEY_FILE": str(executor_private),
        "ASSISTX_EXECUTOR_PUBLIC_KEY_FILE": str(executor_public),
        "ASSISTX_EXECUTOR_KEY_ID": "assistx-executor-v1",
        "ASSISTX_RUNTIME_PROJECTION_PRIVATE_KEY_FILE": str(projection_private),
        "ASSISTX_RUNTIME_PROJECTION_PUBLIC_KEY_FILE": str(projection_public),
        "ASSISTX_RUNTIME_PROJECTION_KEY_ID": "assistx-runtime-projection-v1",
        "ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN": _token(),
        "ASSISTX_EXECUTOR_SERVICE_TOKEN": _token(),
        "AUTO_ROUTER_INTERNAL_SERVICE_TOKEN": _token(),
        "AUTO_ROUTER_ADMIN_TOKEN": _token(),
        "BASIC_AUTH_USER": "reconciliation-admin",
        "BASIC_AUTH_PASS": _token(),
        "AUTO_ROUTER_ASSISTX_BASIC_AUTH_USER": "reconciliation-admin",
        "WS_AUTH_REQUIRED": "1",
        "WS_AUTH_TOKEN": _token(),
        "RECONCILIATION_ASSISTX_NETWORK": "assistx_reconciliation_shared",
    }
    values["AUTO_ROUTER_ASSISTX_BASIC_AUTH_PASS"] = values["BASIC_AUTH_PASS"]

    env_path = output_dir / ".env.reconciliation.generated"
    env_body = "\n".join(
        [
            "# Generated reconciliation credentials. Do not commit this file.",
            "# Add site-specific Neo4j/runtime identity values before deployment.",
            *[f"{key}={value}" for key, value in values.items()],
            "",
        ]
    ).encode("utf-8")
    _write_exclusive(env_path, env_body, PRIVATE_MODE)

    manifest = {
        "schema_version": "1",
        "generated_at": datetime.now(UTC).isoformat(),
        "keypairs": {
            "executor": {
                "key_id": values["ASSISTX_EXECUTOR_KEY_ID"],
                "algorithm": "Ed25519",
                "public_key_sha256": executor_fingerprint,
                "public_key_file": str(executor_public),
            },
            "runtime_projection": {
                "key_id": values["ASSISTX_RUNTIME_PROJECTION_KEY_ID"],
                "algorithm": "Ed25519",
                "public_key_sha256": projection_fingerprint,
                "public_key_file": str(projection_public),
            },
        },
        "separation": {
            "executor_and_projection_keys_distinct": (
                executor_fingerprint != projection_fingerprint
            ),
            "bootstrap_service_internal_and_admin_tokens_distinct": len(
                {
                    values["ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN"],
                    values["ASSISTX_EXECUTOR_SERVICE_TOKEN"],
                    values["AUTO_ROUTER_INTERNAL_SERVICE_TOKEN"],
                    values["AUTO_ROUTER_ADMIN_TOKEN"],
                }
            )
            == 4,
        },
    }
    manifest_path = output_dir / "reconciliation-secrets-manifest.json"
    _write_exclusive(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        PRIVATE_MODE,
    )

    for private_path in (executor_private, projection_private, env_path, manifest_path):
        mode = stat.S_IMODE(private_path.stat().st_mode)
        if mode != PRIVATE_MODE:
            raise RuntimeError(f"private file {private_path} has unsafe mode {mode:o}")

    return {
        "output_dir": output_dir,
        "environment_file": env_path,
        "manifest": manifest_path,
        "executor_public_key": executor_public,
        "runtime_projection_public_key": projection_public,
        "metadata": manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate distinct Ed25519 keypairs and random service tokens for the "
            "AssistX/auto-router reconciliation stack. Existing files are never overwritten."
        )
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Private directory outside the repository for generated credentials",
    )
    args = parser.parse_args()

    result = generate(args.output_dir)
    print(f"credentials directory: {result['output_dir']}")
    print(f"environment file: {result['environment_file']}")
    print(f"public manifest: {result['manifest']}")
    print("secret values were written to disk and were not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
