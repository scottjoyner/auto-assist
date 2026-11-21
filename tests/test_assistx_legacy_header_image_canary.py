"""Static safety contract for original-image-derived AssistX authentication canary."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import assistx_legacy_header_image_canary as canary


def test_inert_default_does_not_trigger_docker_actions():
    ran=subprocess.run([sys.executable,str(canary.__file__)],
                       text=True,capture_output=True,timeout=6,check=True)
    result=json.loads(ran.stdout)
    assert result["decision"]=="HOLD"
    assert result["physical_executed"] is False
    assert result["production_restarts"]==0
    assert result["task_mutations"]==0


def test_pinned_runtime_and_reviewed_source_exact():
    assert canary.API_IMAGE.startswith("sha256:")
    assert canary.HERMES_IMAGE.startswith("sha256:")
    assert len(canary.PINNED_SOURCE_BLOB)==40
    assert canary.SOURCE==canary.ROOT/"src/assistx/api.py"
    assert canary.PROD_API=="assistx-api"
    assert canary.PROD_HERMES=="assistx-hermes-adapter"


def test_synthetic_runtime_probe_checks_default_and_emergency_deny():
    body=canary.PROBE
    for part in (
        'ASSISTX_REJECT_LEGACY_TRUSTED_HEADER',
        'legacy_header_alone_rejected',
        'header_with_valid_basic_rejected',
        'valid_basic_without_header_accepted',
        'invalid_basic_without_header_rejected',
        'legacy_fallback_is_dangerous_when_disabled',
        'X-Synthetic-Trusted-User',
        'synthetic-password',
        '"task_mutations":0',
    ):
        assert part in body
    assert "OPENAI_API_KEY" not in body
    assert "docker" not in body
    assert "http://" not in body and "https://" not in body


def test_image_is_derived_locally_with_no_network_or_host_mounts():
    source=Path(canary.__file__).read_text()
    assert '"docker","tag",API_IMAGE,base_alias' in source
    assert '"docker","build","--network=none","--pull=false"' in source
    assert '"docker","run","--rm","--network","none"' in source
    assert '"--user","65534:65534","--read-only"' in source
    assert '"--cap-drop","ALL","--security-opt","no-new-privileges"' in source
    assert '"--pids-limit","64","--memory","512m","--cpus","1"' in source
    assert '"--entrypoint","python3"' in source
    assert '"docker","image","rm",label' in source
    assert '"docker","image","rm",base_alias' in source
    assert '"docker","image","rm",API_IMAGE' not in source
    assert '"-v"' not in source and '"--mount"' not in source
    assert '"docker","restart"' not in source
    assert '"docker","stop"' not in source
    assert '"docker","exec"' not in source


def test_source_hash_fails_closed_on_unreviewed_auth_code():
    source=Path(canary.__file__).read_text()
    assert '"git","-C",str(ROOT),"hash-object",str(SOURCE)' in source
    assert 'observed.stdout.strip()==PINNED_SOURCE_BLOB' in source
    assert 'exact_runtime(PROD_API,API_IMAGE)' in source
    assert 'exact_runtime(PROD_HERMES,HERMES_IMAGE,16)' in source
