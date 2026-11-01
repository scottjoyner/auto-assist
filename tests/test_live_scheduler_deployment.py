import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "scheduler_deployment_check", ROOT / "scripts/verify-live-scheduler-deployment.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HASH = "a" * 64

def test_equal_code_does_not_imply_admission():
    result = module.evaluate("assistx", HASH, {"sha256": HASH, "mtime_ns": 10}, started_ns=20, must_predate_start=True)
    assert result["status"] == "code_matches"
    assert result["admission_applied"] is False
    assert result["authorized_capacity"] == "not_evaluated"

def test_different_code_is_blocked():
    result = module.evaluate("router", HASH, {"sha256": "b" * 64})
    assert result["status"] == "blocked"
    assert result["reason"] == "deployment_source_drift"

def test_modified_after_start_is_blocked():
    result = module.evaluate("assistx", HASH, {"sha256": HASH, "mtime_ns": 100}, started_ns=20, must_predate_start=True)
    assert result["status"] == "code_matches"

def test_missing_live_evidence_is_blocked():
    result = module.evaluate("router", HASH, None)
    assert result["status"] == "blocked"
    assert result["reason"] == "missing_live_probe"
