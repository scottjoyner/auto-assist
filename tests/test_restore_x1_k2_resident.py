"""Regression gates for fail-closed x1 K2 residency recovery."""

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/restore-x1-k2-resident.py"
spec = importlib.util.spec_from_file_location("restore_k2", SCRIPT)
assert spec and spec.loader
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def configure(monkeypatch, tmp_path, loaded):
    monkeypatch.setattr(restore, "HOLD", tmp_path / "hold")
    monkeypatch.setattr(restore, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(restore, "models", lambda: loaded)


def test_already_resident_is_noop(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, [restore.MODEL_ID])
    monkeypatch.setattr(restore.subprocess, "run", lambda *a, **k: pytest.fail("unexpected model load"))
    assert restore.main() == 0


def test_other_resident_is_not_replaced(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, ["other-active-model"])
    monkeypatch.setattr(restore.subprocess, "run", lambda *a, **k: pytest.fail("unexpected model load"))
    assert restore.main() == 0


def test_low_memory_blocks_unloaded_model(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, [])
    monkeypatch.setattr(restore, "available_memory_bytes", lambda: 0)
    assert restore.main() == 2


def test_operator_hold_blocks_even_probes(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, [])
    restore.HOLD.touch()
    monkeypatch.setattr(restore, "models", lambda: pytest.fail("operator hold must prevent probes"))
    assert restore.main() == 0


def test_does_not_issue_production_admission():
    raw = SCRIPT.read_text()
    assert "approve-runtime-projection.py" not in raw
    assert "NEO4J_PASSWORD" not in raw
    assert "admission unchanged" in raw
