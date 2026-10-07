from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assistx-fleet-admit-live.py"


def test_keepalive_pins_the_real_lms_cli_for_restricted_cron_path():
    text = SCRIPT.read_text()
    assert 'LMS_BIN = Path("/home/scott/.lmstudio/bin/lms")' in text
    assert '[str(LMS_BIN), "ps", "--json"]' in text
    assert '[str(LMS_BIN), "ls", "--json"]' in text
    assert '["lms", "ps", "--json"]' not in text
    assert '["lms", "ls", "--json"]' not in text


def test_keepalive_admits_only_the_exact_reviewed_quality_checkpoint():
    text = SCRIPT.read_text()
    assert "Ornith-1.5-35B-A3B-APEX-MTP-Quality.gguf" in text
    assert "c83373e4c502c6d4929339406ffbb1f442598b153a3ade02b628752e00a282fd" in text
    assert "EXPECTED_ORNITH_SIZE = 23_718_411_552" in text
    assert "expected exactly one resident Ornith Quality instance" in text
    assert "model-x1-ornith-quality-c83373e4" in text
    assert "parallel_slots: 1" in text
    assert "EXPECTED_TOOL_ID" not in text


def test_keepalive_reproves_before_each_short_lease_and_uses_real_v2_export():
    text = SCRIPT.read_text()
    assert "completion_canary(LAN_BASE)" in text
    assert 'content != "OK"' in text
    assert "ttl_seconds: 900" in text
    assert "expected_current_generation" in text
    assert "from assistx.runtime_projection_v2 import build_runtime_projection;" in text
    assert "build_runtime_projection_v2" not in text
    assert 'APPROVE_SCRIPT = Path("/home/scott/git/auto-assist/scripts/approve-runtime-projection.py")' in text
    assert "5df9efa67d2292d978193915e9e9b0a9d04d884d8ba37f758b3b0733c8f800f3" in text
