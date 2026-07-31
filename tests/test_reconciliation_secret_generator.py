from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_reconciliation_secrets",
    ROOT / "scripts" / "generate-reconciliation-secrets.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def test_generator_creates_distinct_scoped_credentials(tmp_path: Path) -> None:
    result = module.generate(tmp_path / "secrets")
    env_path = Path(result["environment_file"])
    manifest_path = Path(result["manifest"])
    values = _env(env_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert Path(values["ASSISTX_EXECUTOR_PRIVATE_KEY_FILE"]).is_file()
    assert Path(values["ASSISTX_RUNTIME_PROJECTION_PRIVATE_KEY_FILE"]).is_file()
    assert (
        values["ASSISTX_EXECUTOR_PRIVATE_KEY_FILE"]
        != values["ASSISTX_RUNTIME_PROJECTION_PRIVATE_KEY_FILE"]
    )
    assert (
        values["ASSISTX_EXECUTOR_PUBLIC_KEY_FILE"]
        != values["ASSISTX_RUNTIME_PROJECTION_PUBLIC_KEY_FILE"]
    )

    tokens = {
        values["ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN"],
        values["ASSISTX_EXECUTOR_SERVICE_TOKEN"],
        values["AUTO_ROUTER_INTERNAL_SERVICE_TOKEN"],
        values["AUTO_ROUTER_ADMIN_TOKEN"],
    }
    assert len(tokens) == 4
    assert all(len(token) >= 48 for token in tokens)
    assert manifest["separation"]["executor_and_projection_keys_distinct"] is True
    assert (
        manifest["separation"][
            "bootstrap_service_internal_and_admin_tokens_distinct"
        ]
        is True
    )

    for private_path in (
        env_path,
        manifest_path,
        Path(values["ASSISTX_EXECUTOR_PRIVATE_KEY_FILE"]),
        Path(values["ASSISTX_RUNTIME_PROJECTION_PRIVATE_KEY_FILE"]),
    ):
        assert stat.S_IMODE(private_path.stat().st_mode) == 0o600

    assert stat.S_IMODE(
        Path(values["ASSISTX_EXECUTOR_PUBLIC_KEY_FILE"]).stat().st_mode
    ) == 0o644
    assert stat.S_IMODE(
        Path(values["ASSISTX_RUNTIME_PROJECTION_PUBLIC_KEY_FILE"]).stat().st_mode
    ) == 0o644


def test_generator_refuses_to_overwrite_existing_credentials(tmp_path: Path) -> None:
    output = tmp_path / "secrets"
    module.generate(output)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        module.generate(output)
