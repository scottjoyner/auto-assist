"""Static, synthetic K2 compatibility tests; no real model load or API POST."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import json
import subprocess

SRC = Path(__file__).resolve().parents[1] / "scripts/check-x1-k2-compatibility.py"
SPEC = importlib.util.spec_from_file_location("x1k2", SRC)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def _ready_overrides():
    return {
        "sha256_file": lambda path: (
            m.EXPECTED_SHA256 if path == m.MODEL else "f" * 64
        ),
        "available_memory_bytes": lambda: 16 * 1024**3,
        "root_free_bytes": lambda: 25 * 1024**3,
        "api_catalog": lambda: {"available": True, "loaded_models": 0,
                                "expected_model_loaded": False},
        "estimate_only": lambda: {"status": "estimate_succeeded",
                                  "estimated_total_gib": 5.13},
    }


def test_version_drift_is_explicit_hold():
    class P:
        def read_text(self):
            return json.dumps({"version": "0.4.28+2"})
    with patch.object(m, "APP_PACKAGE", P()):
        with patch.dict(m.observe.__globals__, _ready_overrides()):
            with patch.object(m.Path, "is_file", return_value=True):
                result = m.observe(with_estimate=True)
    assert result["reviewed_version_match"] is False
    assert result["model_sha256_match"] is True
    assert result["estimate"]["estimated_total_gib"] == 5.13
    assert result["gate"] == "HOLD_REQUIRES_OPERATOR_REVIEW"
    assert result["can_dispatch"] is False
    assert result["model_calls"] == 0
    assert result["model_loads"] == 0
    assert result["approval_issued"] is False


def test_even_all_green_stays_non_admitting():
    class P:
        def read_text(self):
            return json.dumps({"version": "0.4.26+4"})
    with patch.object(m, "APP_PACKAGE", P()):
        with patch.dict(m.observe.__globals__, _ready_overrides()):
            with patch.object(m.Path, "is_file", return_value=True):
                r = m.observe(with_estimate=False)
    assert r["reviewed_version_match"]
    assert not r["can_dispatch"]
    assert r["gate"] == "HOLD_REQUIRES_OPERATOR_REVIEW"
    assert "isolated_decode_canary_and_signed_approval_not_proven" in r["reasons"]


def test_estimate_cli_is_fixed_command_without_load():
    fake = subprocess.CompletedProcess([], 0, stdout="",
                                       stderr="Estimated Total Memory: 5.13 GiB")
    with patch.object(m.CLI.__class__, "is_file", return_value=True):
        with patch.object(m.subprocess, "run", return_value=fake) as runner:
            r = m.estimate_only()
    assert r == {"status": "estimate_succeeded", "estimated_total_gib": 5.13}
    argv = runner.call_args.args[0]
    assert argv[-1] == "--estimate-only"
    assert "--yes" not in argv
    assert "--auto" not in argv
    assert runner.call_args.kwargs["env"]["HOME"] == "/home/scott"


def test_failed_estimate_never_infers_mem():
    fake = subprocess.CompletedProcess([], 1, stdout="", stderr="untrusted-private-error")
    with patch.object(m.CLI.__class__, "is_file", return_value=True):
        with patch.object(m.subprocess, "run", return_value=fake):
            result = m.estimate_only()
    assert result["status"] == "rejected_by_cli"
    assert result["estimated_total_gib"] is None


def test_api_fails_closed_on_error_body_and_redirect():
    assert m.NoRedirect().redirect_request(None, None, 302, "moved", {},
                                            "https://attacker.example") is None
    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *x): return None
        def read(self, n): return b'{"error":"Unsupported endpoint"}'
    class O:
        def open(self, req, timeout): return Resp()
    with patch.object(m.urllib.request, "build_opener", return_value=O()):
        try: m.api_catalog()
        except ValueError: pass
        else: raise AssertionError("error body accepted")


def test_bad_artifact_never_changes_admission():
    class P:
        def read_text(self): return json.dumps({"version": "0.4.26+4"})
    with patch.object(m, "APP_PACKAGE", P()):
        with patch.dict(m.observe.__globals__, {**_ready_overrides(),
                "sha256_file": lambda path: "bad"}):
            with patch.object(m.Path, "is_file", return_value=True):
                r = m.observe(with_estimate=True)
    assert not r["model_sha256_match"]
    assert "model_sha256_match_failed" in r["reasons"]
    assert r["neo4j_writes"] == 0
