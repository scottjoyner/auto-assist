#!/usr/bin/env python3
"""Focused operational verification for read-only supervisor (post 0d47a0fb)."""
import json, os, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
import free_subagent_supervisor as s

FIXTURE_MODELS = pathlib.Path(__file__).parent.parent / "tests" / "fixtures" / "openrouter_models_sample.jsonl"
FIXTURE_STATE = pathlib.Path(__file__).parent.parent / "tests" / "fixtures" / "subagent_state_sample.jsonl"

def test_free_cost_gating():
    models = s.enumerate_free_models(FIXTURE_MODELS)
    ids = [m.get("id") for m in models]
    assert any("claude-3.5-sonnet" in i for i in ids), "free model missing"
    assert any("gemini-2.5-flash" in i for i in ids), "free model missing"
    assert not any("deepseek-r1" in i for i in ids), "paid model leaked"
    assert not any("zero-prompt-paid-completion" in i for i in ids), "completion-cost gate failed"
    print("PASS: free-cost gating")

def test_duplicate_classification():
    dups = s.detect_duplicate_worktrees(s.load_state(FIXTURE_STATE))
    assert len(dups) == 1, f"expected 1 duplicate worktree, got {len(dups)}"
    assert dups[0]["worktree"] == "/tmp/wt-a"
    print("PASS: duplicate classification")

def test_looping_classification():
    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
        path = pathlib.Path(tf.name)
    try:
        s.register_subagent({"title":"L1","objective":"o","model":"m","worktree":"/tmp/wt-l","pid":1,"session":"sess-loop","status":"active"}, path)
        s.register_subagent({"title":"L2","objective":"o","model":"m","worktree":"/tmp/wt-l","pid":2,"session":"sess-loop","status":"running"}, path)
        loops = s.detect_looping_records(s.load_state(path))
        assert len(loops) == 1, f"expected 1 loop, got {len(loops)}"
        assert loops[0]["session"] == "sess-loop"
        print("PASS: looping classification")
    finally:
        path.unlink(missing_ok=True)

def test_stale_classification():
    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
        path = pathlib.Path(tf.name)
        tf.write(json.dumps({"title":"S","objective":"o","model":"m","worktree":"/tmp/wt-s","pid":99,"session":"sess-stale","status":"completed","updated_at":"2026-09-01T00:00:00Z"}) + "\n")
    try:
        stale = s.detect_stale_records(s.load_state(path), max_age_minutes=30)
        assert len(stale) >= 1, f"expected stale records, got {len(stale)}"
        print("PASS: stale classification")
    finally:
        path.unlink(missing_ok=True)

def test_projection_readonly_and_deterministic():
    proj1 = s.emit_projection(free_models=s.enumerate_free_models(FIXTURE_MODELS), state_path=FIXTURE_STATE)
    proj2 = s.emit_projection(free_models=s.enumerate_free_models(FIXTURE_MODELS), state_path=FIXTURE_STATE)
    assert proj1.get("read_only") is True
    assert proj1.get("suggested_scale_verdict") == "hold"
    assert proj1.get("duplicate_worktrees") is not None
    assert proj1.get("looping_records") is not None
    assert proj1.get("stale_records") is not None
    # Deterministic: same inputs -> identical JSON
    assert json.dumps(proj1, sort_keys=True) == json.dumps(proj2, sort_keys=True), "non-deterministic projection"
    note = (proj1.get("projections_note") or "").lower()
    assert "/dev/null" in note or "stdin" in note or "close stdin" in note, "stdin hazard undocumented"
    assert "free-cost" in note or "zero" in note or "pricing" in note, "free-cost gating undocumented"
    print("PASS: projection read-only + deterministic + note checks")

def live_snapshot():
    proj = s.emit_projection(free_models=s.enumerate_free_models(FIXTURE_MODELS), state_path=FIXTURE_STATE)
    snap = pathlib.Path("artifacts/free_subagent_supervisor_snapshot.json")
    snap.parent.mkdir(parents=True, exist_ok=True)
    with snap.open("w") as f:
        json.dump(proj, f, sort_keys=True, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"PASS: live snapshot written to {snap} (verdict={proj.get('suggested_scale_verdict')})")

if __name__ == "__main__":
    test_free_cost_gating()
    test_duplicate_classification()
    test_looping_classification()
    test_stale_classification()
    test_projection_readonly_and_deterministic()
    live_snapshot()
    print("\nAll focused operational verifications passed. No killing/routing/dispatch/merge/push performed.")
