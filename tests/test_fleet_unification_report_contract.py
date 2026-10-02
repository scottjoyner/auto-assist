from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).parents[1]
CANONICAL = ROOT / "scripts" / "fleet-unification-report.sh"
INSTALLED = Path.home() / ".hermes" / "scripts" / "fleet-unification-report.sh"


def test_installed_transition_script_matches_managed_source() -> None:
    assert CANONICAL.is_file()
    assert INSTALLED.is_file()
    assert hashlib.sha256(CANONICAL.read_bytes()).hexdigest() == hashlib.sha256(INSTALLED.read_bytes()).hexdigest()


def test_transition_script_keeps_full_collection_and_silent_unchanged_path() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "verify-fleet-unification" in text
    assert "Path(state_file).write_text" in text
    assert "if not changes:" in text
    assert "raise SystemExit(0)" in text
    assert "Full report:" in text
