import hashlib
import re
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
    assert re.search(r'APPROVE_SCRIPT_SHA256 = "[0-9a-f]{64}"', text)


def test_keepalive_pinned_approval_checksum_matches_the_approver_this_tree_ships():
    """The pin must equal the approver shipped in the same tree, or the tool is dead.

    assistx-fleet-admit-live.py refuses to execute when the approval script's
    checksum differs from its pinned constant, and that refusal is the right
    behaviour: it is the only thing binding "which code approved this generation"
    to a specific artifact. But it also means a change that edits
    scripts/approve-runtime-projection.py without updating the pin lands a tool
    that can never approve anything -- fail-closed, and silently so, because it
    fails at run time rather than at review time.

    Asserting the constant is merely present does not catch that. It has to equal
    the file. This is the assertion that was missing.
    """
    text = SCRIPT.read_text()
    match = re.search(r'APPROVE_SCRIPT_SHA256 = "([0-9a-f]{64})"', text)
    assert match, "assistx-fleet-admit-live.py no longer pins its approval script"

    approver = ROOT / "scripts" / "approve-runtime-projection.py"
    assert approver.is_file(), "the pinned approval script is absent from this tree"

    actual = hashlib.sha256(approver.read_bytes()).hexdigest()
    assert match.group(1) == actual, (
        "assistx-fleet-admit-live.py pins an approval-script checksum that does not "
        "match the approver shipped alongside it, so it would fail closed with "
        "'approval script checksum drifted' on its first run and could never "
        f"approve anything (pinned={match.group(1)}, actual={actual})"
    )
