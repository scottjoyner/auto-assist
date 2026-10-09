"""Fail-closed deploy preflight, tested only in a disposable fake Git tree."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess


SOURCE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy-kipnerter-tailnet-gateway.sh"


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], check=True, text=True,
                         capture_output=True)
    return out.stdout.strip()


def _fixture(tmp_path: Path):
    root=tmp_path/"repo"
    scripts=root/"scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SOURCE_SCRIPT, scripts/SOURCE_SCRIPT.name)
    # The real configuration script is never invoked by this test.
    (scripts/"configure-kipnerter-tailnet-serve.sh").write_text(
        "#!/bin/sh\\necho applied > /tmp/UNSAFE_TEST_MARKER_DO_NOT_CREATE\\n")
    (scripts/"verify-kipnerter-tailnet-gateway.sh").write_text(
        "#!/bin/sh\\nexit 0\\n")
    _git(root, "init", "-b", "main")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@invalid",
         "commit", "-m", "synthetic")
    sha=_git(root, "rev-parse", "HEAD")
    witness=tmp_path/"independent-witness.json"
    witness.write_text(json.dumps({
        "schema": "assistx-ingress-negative-v1", "source_sha": sha,
        "independent_reviewer":"test-reviewer", "spoofed_identity_header_denied":True,
        "legacy_caddy_header_stripping_verified":True,
        "loopback_nonproxy_denied":True, "mobile_path_scope_checked":True,
        "operator_approval":True,
    }))
    env={
        **os.environ,
        "ASSISTX_API_BIND":"127.0.0.1",
        "TRUSTED_AUTH_HEADER":"Tailscale-User-Login",
        "KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE":"0",
        "KIPNERTER_TAILNET_ALLOWED_LOGINS":"synthetic@example.invalid",
        "KIPNERTER_GATEWAY_SOURCE_SHA":sha,
        "KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED":"1",
        "KIPNERTER_GATEWAY_NEGATIVE_WITNESS_FILE":str(witness),
        "KIPNERTER_GATEWAY_APPLY":"0",
        "KIPNERTER_GATEWAY_APPLY_OPERATOR_APPROVED":"0",
    }
    return root,scripts,witness,env


def _run(root:Path, env:dict):
    return subprocess.run(["bash",str(root/"scripts"/SOURCE_SCRIPT.name)],cwd=root,
                          env=env,text=True,capture_output=True,timeout=8)


def test_preflight_with_full_synthetic_witness_is_read_only(tmp_path):
    root,scripts,witness,env=_fixture(tmp_path)
    before=_git(root,"status","--porcelain")
    result=_run(root,env)
    assert result.returncode == 0, result.stderr
    assert "apply=false; no Serve changes" in result.stdout
    assert _git(root,"status","--porcelain") == before


def test_preflight_denies_incorrect_source_and_dirty_checkout(tmp_path):
    root,scripts,witness,env=_fixture(tmp_path)
    assert _run(root,{**env,"KIPNERTER_GATEWAY_SOURCE_SHA":"f"*40}).returncode != 0
    assert _run(root,{**env,"KIPNERTER_GATEWAY_SOURCE_SHA":"main"}).returncode != 0
    (root/"untracked-file").write_text("not staged")
    result=_run(root,env)
    assert result.returncode != 0
    assert "working tree is dirty" in result.stderr


def test_preflight_denies_missing_caddy_provenance_and_witness(tmp_path):
    root,scripts,witness,env=_fixture(tmp_path)
    assert _run(root,{**env,"KIPNERTER_LEGACY_CADDY_FENCE_CONFIRMED":"0"}).returncode != 0
    assert _run(root,{**env,"KIPNERTER_GATEWAY_NEGATIVE_WITNESS_FILE":""}).returncode != 0
    witness.write_text(json.dumps({"schema":"assistx-ingress-negative-v1",
                                   "source_sha":env["KIPNERTER_GATEWAY_SOURCE_SHA"],
                                   "independent_reviewer":"test-reviewer",
                                   "spoofed_identity_header_denied":False}))
    assert _run(root,env).returncode != 0


def test_preflight_never_applies_without_separate_operator_approval(tmp_path):
    root,scripts,witness,env=_fixture(tmp_path)
    result=_run(root,{**env,"KIPNERTER_GATEWAY_APPLY":"1"})
    assert result.returncode != 0
    assert "separate operator apply approval missing" in result.stderr


def test_preflight_refuses_empty_allowlist_and_wildcard_bind(tmp_path):
    root,scripts,witness,env=_fixture(tmp_path)
    assert _run(root,{**env,"KIPNERTER_TAILNET_ALLOWED_LOGINS":""}).returncode != 0
    assert _run(root,{**env,"ASSISTX_API_BIND":"0.0.0.0"}).returncode != 0
    assert _run(root,{**env,"KIPNERTER_AGENT_ALLOW_MODEL_OVERRIDE":"1"}).returncode != 0
