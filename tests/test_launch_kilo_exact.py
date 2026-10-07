from __future__ import annotations

import importlib.util
import json
import pathlib
import sqlite3
import subprocess
import tempfile
from unittest import mock

MODULE_PATH = pathlib.Path(__file__).parent.parent / "scripts" / "launch_kilo_exact.py"
SPEC = importlib.util.spec_from_file_location("launch_kilo_exact", MODULE_PATH)
launch_kilo_exact = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(launch_kilo_exact)


def _write_qualification(
    state_path: pathlib.Path,
    model: str,
    *,
    started_at: str | None = None,
    qualified: bool = True,
    identity: bool = True,
    capacity: str = "usable",
    response_model: str | None = None,
    cost: float | None = 0.0,
    upstream_cost: float | None = 0.0,
    structured_tool_call: bool = True,
) -> pathlib.Path:
    ledger = state_path.parent / "provider-qualification.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema": "free-provider-qualification/v1",
        "provider": "kilo_free",
        "requested_model": model,
        "response_model": model if response_model is None else response_model,
        "qualified": qualified,
        "model_identity_complete": identity,
        "capacity_state": capacity,
        "structured_tool_call": structured_tool_call,
        "cost": cost,
        "upstream_inference_cost": upstream_cost,
        "started_at": started_at or launch_kilo_exact._now_utc(),
    }
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return ledger


def test_bootstrap_models_are_exact_but_launch_requires_dynamic_evidence(tmp_path):
    expected = {
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "cohere/north-mini-code:free",
        "dots-studio/dots-3-note-preview:free",
        "poolside/laguna-s-2.1:free",
        "liquid/lfm-2.5-2.6b:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "nvidia/nemotron-3.5-lightning:free",
        "inclusionai/ling-3.1-flash",
    }
    assert launch_kilo_exact.QUALIFIED_EXACT_MODELS == expected
    state = tmp_path / "state" / "live.jsonl"
    for model in expected:
        assert launch_kilo_exact.route_kind(model) == "exact"
        _write_qualification(state, model)
        launch_kilo_exact.validate_model(model, state)


def test_alias_and_unknown_models_fail_closed(tmp_path):
    state = tmp_path / "state" / "live.jsonl"
    for model in ("openrouter/free", "kilo/auto", "auto", "free"):
        try:
            launch_kilo_exact.validate_model(model, state)
            raise AssertionError(model)
        except ValueError:
            pass
    try:
        launch_kilo_exact.validate_model("some-paid/model", state)
        raise AssertionError("unknown model should fail")
    except ValueError as exc:
        assert "qualification" in str(exc)


def test_dynamic_qualification_rejects_stale_failed_and_mismatched(tmp_path):
    state = tmp_path / "state" / "live.jsonl"
    model = "brand/new-code-model:free"

    _write_qualification(state, model, started_at="2000-01-01T00:00:00Z")
    ok, reason = launch_kilo_exact.qualification_allows_launch(model, state)
    assert ok is False
    assert reason == "stale_qualification"

    _write_qualification(state, model, qualified=False)
    ok, reason = launch_kilo_exact.qualification_allows_launch(model, state)
    assert ok is False
    assert reason == "not_qualified"

    _write_qualification(state, model, response_model="brand/other-model:free")
    ok, reason = launch_kilo_exact.qualification_allows_launch(model, state)
    assert ok is False
    assert reason == "response_model_mismatch"

    _write_qualification(state, model, upstream_cost=None)
    ok, reason = launch_kilo_exact.qualification_allows_launch(model, state)
    assert ok is False
    assert reason == "upstream_inference_cost_unknown"

    _write_qualification(state, model)
    ok, reason = launch_kilo_exact.qualification_allows_launch(model, state)
    assert ok is True
    assert reason == "qualified_exact_zero_cost"
    launch_kilo_exact.validate_model(model, state)


def test_latest_qualification_wins(tmp_path):
    state = tmp_path / "state" / "live.jsonl"
    model = "brand/latest-wins:free"
    _write_qualification(state, model, qualified=True)
    _write_qualification(state, model, qualified=False)
    assert launch_kilo_exact.qualification_allows_launch(model, state)[0] is False


def test_qualified_models_from_ledger_is_dynamic(tmp_path):
    state = tmp_path / "state" / "live.jsonl"
    _write_qualification(state, "brand/dynamic-a:free")
    _write_qualification(state, "brand/dynamic-b:free", qualified=False)
    assert launch_kilo_exact.qualified_models_from_ledger(state) == [
        "brand/dynamic-a:free"
    ]


def test_build_command_matches_proven_live_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_BIN", "/opt/opencode")
    worktree = pathlib.Path("/tmp/trace-worktree")
    state = tmp_path / "state" / "live.jsonl"
    _write_qualification(state, "cohere/north-mini-code:free")
    cmd = launch_kilo_exact.build_command(
        "cohere/north-mini-code:free",
        "trace-title",
        "do useful work",
        worktree,
        state,
    )
    assert cmd == [
        "/opt/opencode",
        "run",
        "--auto",
        "--format",
        "json",
        "--title",
        "trace-title",
        "--dir",
        str(worktree.resolve()),
        "-m",
        "kilo_free/cohere/north-mini-code:free",
        "do useful work",
    ]


def _make_session_db(
    path: pathlib.Path,
    *,
    title: str,
    directory: str,
    sid: str = "ses-real",
    model_id: str = "nvidia/nemotron-3-super-120b-a12b:free",
    created_ms: int = 1,
):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE session "
        "(id TEXT, title TEXT, directory TEXT, model TEXT, time_created INTEGER)"
    )
    con.execute(
        "INSERT INTO session VALUES (?,?,?,?,?)",
        (
            sid,
            title,
            directory,
            json.dumps({"providerID": "kilo_free", "id": model_id}),
            created_ms,
        ),
    )
    con.commit()
    con.close()


def test_session_id_from_jsonl_reads_real_opencode_event(tmp_path):
    stdout = tmp_path / "run.jsonl"
    stdout.write_text(
        json.dumps({"type": "step_start", "sessionID": "ses-from-jsonl"}) + "\n"
    )
    assert launch_kilo_exact._session_id_from_jsonl(stdout) == "ses-from-jsonl"


def test_wait_for_session_requires_exact_worktree_directory():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        worktree = root / "worktree"
        worktree.mkdir()
        db = root / "opencode.db"
        stdout = root / "run.jsonl"
        stdout.write_text(
            json.dumps({"type": "step_start", "sessionID": "ses-real-123"}) + "\n"
        )
        _make_session_db(
            db,
            title="real-title",
            directory=str(worktree),
            sid="ses-real-123",
            model_id="cohere/north-mini-code:free",
            created_ms=100,
        )
        assert launch_kilo_exact.wait_for_session(
            db,
            title="real-title",
            model_id="cohere/north-mini-code:free",
            expected_directory=worktree,
            stdout_path=stdout,
            not_before_ms=0,
            timeout_seconds=0.2,
        ) == "ses-real-123"


def test_wait_for_session_rejects_emitted_session_outside_worktree():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        worktree = root / "worktree"
        worktree.mkdir()
        db = root / "opencode.db"
        stdout = root / "run.jsonl"
        stdout.write_text(
            json.dumps({"type": "step_start", "sessionID": "ses-escaped"}) + "\n"
        )
        _make_session_db(
            db,
            title="escaped-title",
            directory="/home/scott",
            sid="ses-escaped",
            model_id="cohere/north-mini-code:free",
            created_ms=100,
        )

        try:
            launch_kilo_exact.wait_for_session(
                db,
                title="escaped-title",
                model_id="cohere/north-mini-code:free",
                expected_directory=worktree,
                stdout_path=stdout,
                not_before_ms=0,
                timeout_seconds=0.2,
            )
            raise AssertionError("escaped session should be rejected")
        except RuntimeError as exc:
            assert "directory mismatch" in str(exc)
            assert str(worktree.resolve()) in str(exc)


def test_failure_classification():
    assert launch_kilo_exact.classify_failure("HTTP 429 rate limit exceeded") == "rate_limited"
    assert launch_kilo_exact.classify_failure("402 payment required") == "payment_required"
    assert launch_kilo_exact.classify_failure("monthly quota exhausted") == "quota_exhausted"
    assert launch_kilo_exact.classify_failure("401 Unauthorized") == "auth_error"
    assert launch_kilo_exact.classify_failure("boom") == "process_error"


def test_dry_run_does_not_spawn_process(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_BIN", "/opt/opencode")
    _write_qualification(
        tmp_path / "state.jsonl",
        "nvidia/nemotron-3-super-120b-a12b:free",
    )
    with mock.patch.object(subprocess, "Popen") as popen:
        result = launch_kilo_exact.launch(
            model_id="nvidia/nemotron-3-super-120b-a12b:free",
            objective="test objective",
            worktree=tmp_path,
            state_path=tmp_path / "state.jsonl",
            trace_dir=tmp_path / "traces",
            db_path=tmp_path / "opencode.db",
            supervisor_path=MODULE_PATH.parent / "free_subagent_supervisor.py",
            exporter_path=MODULE_PATH.parent / "export_opencode_session_traces.py",
            title="dry-run-title",
            dry_run=True,
        )
    popen.assert_not_called()
    assert result["dry_run"] is True
    assert result["effective_model"] == "nvidia/nemotron-3-super-120b-a12b:free"
    assert result["stdin"] == "/dev/null"


def test_launch_uses_real_child_pid_and_real_session_id(tmp_path, monkeypatch):
    db = tmp_path / "opencode.db"
    _make_session_db(db, title="live-title", directory=str(tmp_path), sid="ses-from-db")

    class FakeProc:
        pid = 4242
        returncode = 0
        def poll(self):
            return 0
        def wait(self, timeout=None):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    fake_proc = FakeProc()
    monkeypatch.setenv("OPENCODE_BIN", "/opt/opencode")
    _write_qualification(tmp_path / "state.jsonl", "cohere/north-mini-code:free")
    with (
        mock.patch.object(subprocess, "Popen", return_value=fake_proc) as popen,
        mock.patch.object(
            launch_kilo_exact, "wait_for_session", return_value="ses-from-db"
        ),
        mock.patch.object(launch_kilo_exact, "_register_initial") as register,
        mock.patch.object(launch_kilo_exact, "_update_state") as update,
        mock.patch.object(
            launch_kilo_exact,
            "_final_session_trace",
            return_value={
                "session_id": "ses-from-db",
                "effective_model": "cohere/north-mini-code:free",
            },
        ),
    ):
        register.return_value = {"is_git": False}
        result = launch_kilo_exact.launch(
            model_id="cohere/north-mini-code:free",
            objective="test objective",
            worktree=tmp_path,
            state_path=tmp_path / "state.jsonl",
            trace_dir=tmp_path / "traces",
            db_path=db,
            supervisor_path=MODULE_PATH.parent / "free_subagent_supervisor.py",
            exporter_path=MODULE_PATH.parent / "export_opencode_session_traces.py",
            title="live-title",
        )

    launch_call = next(
        call for call in popen.call_args_list
        if call.kwargs.get("cwd") == tmp_path.resolve()
        and call.kwargs.get("stdin") is subprocess.DEVNULL
    )
    kwargs = launch_call.kwargs
    assert kwargs["cwd"] == tmp_path.resolve()
    assert kwargs["env"]["PWD"] == str(tmp_path.resolve())
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert result["opencode_pid"] == 4242
    assert result["session_id"] == "ses-from-db"
    assert result["success"] is True
    assert register.call_args.kwargs["opencode_pid"] == 4242
    assert register.call_args.kwargs["session_id"] == "ses-from-db"
    update.assert_called_once()

def test_provider_overload_classification():
    assert launch_kilo_exact.classify_failure("HTTP 503 Service temporarily overloaded") == "provider_overloaded"


def test_manifest_is_private_and_hashes_raw_traces(tmp_path):
    state = tmp_path / "state" / "live.jsonl"
    traces = tmp_path / "state" / "traces"
    traces.mkdir(parents=True)
    stdout = traces / "run.stdout.jsonl"
    stderr = traces / "run.stderr.log"
    stdout.write_text('{"event":"ok"}\n')
    stderr.write_text("")

    manifest = launch_kilo_exact._write_manifest(
        state_path=state,
        session_id="ses-manifest",
        title="manifest-test",
        model_id="poolside/laguna-s-2.1:free",
        worktree=tmp_path,
        launch_git={"is_git": False},
        launch_timestamp="2026-10-06T22:00:00Z",
        stdout_path=stdout,
        stderr_path=stderr,
        final_trace={
            "provider": "kilo_free",
            "requested_model": "poolside/laguna-s-2.1:free",
            "resolved_model": None,
            "effective_model": "poolside/laguna-s-2.1:free",
            "model_attribution": "exact-request",
            "model_identity_complete": True,
            "git": {"is_git": False},
            "tokens": {"input": 10, "output": 2},
            "cost": 0,
            "tools": {"bash": 1},
            "patch_parts": 0,
            "last_finish_reason": "stop",
        },
        status="completed",
        failure_class=None,
    )

    payload = json.loads(manifest.read_text())
    assert payload["session_id"] == "ses-manifest"
    assert payload["effective_model"] == "poolside/laguna-s-2.1:free"
    assert payload["stdout"]["sha256"]
    assert payload["stdout"]["bytes"] == len(stdout.read_bytes())
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert manifest.parent.stat().st_mode & 0o777 == 0o700

def test_default_state_root_uses_account_home_not_process_home(monkeypatch):
    monkeypatch.delenv("ASSIST_SUBAGENT_STATE_ROOT", raising=False)
    monkeypatch.setenv("HOME", "/tmp/fake-isolated-home")
    expected = pathlib.Path(launch_kilo_exact.pwd.getpwuid(launch_kilo_exact.os.getuid()).pw_dir) / ".local" / "state" / "auto-assist" / "subagents"
    assert launch_kilo_exact._state_root() == expected


def test_state_root_env_override(monkeypatch, tmp_path):
    override = tmp_path / "custom-state"
    monkeypatch.setenv("ASSIST_SUBAGENT_STATE_ROOT", str(override))
    assert launch_kilo_exact._state_root() == override


def test_final_session_trace_filters_without_exporter_session_argument(tmp_path):
    class FakeExporter:
        @staticmethod
        def export_sessions(db_path):
            assert db_path == tmp_path / "opencode.db"
            return [
                {"session_id": "ses-other", "tokens": {"input": 1}},
                {"session_id": "ses-target", "tokens": {"input": 2}},
            ]

    with mock.patch.object(
        launch_kilo_exact, "_load_module", return_value=FakeExporter()
    ):
        result = launch_kilo_exact._final_session_trace(
            tmp_path / "exporter.py",
            tmp_path / "opencode.db",
            "ses-target",
        )

    assert result == {"session_id": "ses-target", "tokens": {"input": 2}}


def test_budget_monitor_stops_no_progress_run(tmp_path):
    class FakeProc:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    metrics = {
        "input_tokens": 80_000,
        "output_tokens": 100,
        "reasoning_tokens": 10_000,
        "cache_read_tokens": 0,
        "tool_calls": 12,
        "mutation_tool_calls": 0,
        "patch_parts": 0,
    }
    proc = FakeProc()
    with (
        mock.patch.object(
            launch_kilo_exact,
            "_session_budget_metrics",
            return_value=dict(metrics),
        ),
        mock.patch.object(
            launch_kilo_exact,
            "_has_worktree_progress",
            return_value=False,
        ),
    ):
        returncode, reason, observed = launch_kilo_exact._wait_with_budgets(
            proc,
            db_path=tmp_path / "missing.db",
            session_id="ses-budget",
            worktree=tmp_path,
            launch_git={"is_git": False},
            max_wall_seconds=600,
            max_input_tokens=500_000,
            max_reasoning_tokens=200_000,
            max_tool_calls=80,
            max_no_progress_input_tokens=75_000,
            poll_seconds=0.01,
        )

    assert returncode == -15
    assert reason == "budget_no_progress"
    assert observed["input_tokens"] == 80_000
    assert observed["worktree_progress"] is False


def test_budget_monitor_allows_completed_run(tmp_path):
    class FakeProc:
        returncode = 0

        def poll(self):
            return 0

    metrics = {
        "input_tokens": 10_000,
        "output_tokens": 100,
        "reasoning_tokens": 500,
        "cache_read_tokens": 0,
        "tool_calls": 3,
        "mutation_tool_calls": 1,
        "patch_parts": 0,
    }
    with (
        mock.patch.object(
            launch_kilo_exact,
            "_session_budget_metrics",
            return_value=dict(metrics),
        ),
        mock.patch.object(
            launch_kilo_exact,
            "_has_worktree_progress",
            return_value=True,
        ),
    ):
        returncode, reason, observed = launch_kilo_exact._wait_with_budgets(
            FakeProc(),
            db_path=tmp_path / "missing.db",
            session_id="ses-complete",
            worktree=tmp_path,
            launch_git={"is_git": False},
            max_wall_seconds=600,
            max_input_tokens=500_000,
            max_reasoning_tokens=200_000,
            max_tool_calls=80,
            max_no_progress_input_tokens=75_000,
            poll_seconds=0.01,
        )

    assert returncode == 0
    assert reason is None
    assert observed["worktree_progress"] is True


def test_final_session_trace_filters_exporter_without_session_argument(tmp_path, monkeypatch):
    exporter = tmp_path / "exporter.py"
    exporter.write_text(
        "def export_sessions(db_path):\n"
        "    return [\n"
        "        {'session_id': 'ses-other', 'cost': 0},\n"
        "        {'session_id': 'ses-target', 'cost': 0},\n"
        "    ]\n"
    )
    result = launch_kilo_exact._final_session_trace(
        exporter, tmp_path / "unused.db", "ses-target"
    )
    assert result == {"session_id": "ses-target", "cost": 0}


def test_wait_with_budgets_stops_no_progress_run(monkeypatch, tmp_path):
    class FakeProc:
        returncode = None
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = -15
        def wait(self, timeout=None):
            return self.returncode
        def kill(self):
            self.returncode = -9

    proc = FakeProc()
    monkeypatch.setattr(
        launch_kilo_exact,
        "_session_budget_metrics",
        lambda *_args, **_kwargs: {
            "input_tokens": 80_000,
            "output_tokens": 100,
            "reasoning_tokens": 5_000,
            "cache_read_tokens": 0,
            "tool_calls": 12,
            "mutation_tool_calls": 0,
            "patch_parts": 0,
        },
    )
    monkeypatch.setattr(launch_kilo_exact, "_has_worktree_progress", lambda *_a, **_k: False)
    code, reason, metrics = launch_kilo_exact._wait_with_budgets(
        proc,
        db_path=tmp_path / "db",
        session_id="ses",
        worktree=tmp_path,
        launch_git={"is_git": False},
        max_wall_seconds=600,
        max_input_tokens=500_000,
        max_reasoning_tokens=200_000,
        max_tool_calls=80,
        max_no_progress_input_tokens=75_000,
        poll_seconds=0.01,
    )
    assert code == -15
    assert reason == "budget_no_progress"
    assert metrics["worktree_progress"] is False


def test_wait_with_budgets_stops_hard_input_cap_even_with_progress(monkeypatch, tmp_path):
    class FakeProc:
        returncode = None
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = -15
        def wait(self, timeout=None):
            return self.returncode
        def kill(self):
            self.returncode = -9

    proc = FakeProc()
    monkeypatch.setattr(
        launch_kilo_exact,
        "_session_budget_metrics",
        lambda *_args, **_kwargs: {
            "input_tokens": 500_000,
            "output_tokens": 2_000,
            "reasoning_tokens": 40_000,
            "cache_read_tokens": 0,
            "tool_calls": 20,
            "mutation_tool_calls": 3,
            "patch_parts": 2,
        },
    )
    monkeypatch.setattr(launch_kilo_exact, "_has_worktree_progress", lambda *_a, **_k: True)
    code, reason, metrics = launch_kilo_exact._wait_with_budgets(
        proc,
        db_path=tmp_path / "db",
        session_id="ses",
        worktree=tmp_path,
        launch_git={"is_git": False},
        max_wall_seconds=600,
        max_input_tokens=500_000,
        max_reasoning_tokens=200_000,
        max_tool_calls=80,
        max_no_progress_input_tokens=75_000,
        poll_seconds=0.01,
    )
    assert code == -15
    assert reason == "budget_input_tokens"
    assert metrics["worktree_progress"] is True


def test_wait_with_budgets_allows_normal_completion(monkeypatch, tmp_path):
    class FakeProc:
        returncode = 0
        def poll(self):
            return 0
        def terminate(self):
            raise AssertionError("completed process should not be terminated")
        def wait(self, timeout=None):
            return 0
        def kill(self):
            raise AssertionError("completed process should not be killed")

    monkeypatch.setattr(
        launch_kilo_exact,
        "_session_budget_metrics",
        lambda *_args, **_kwargs: {
            "input_tokens": 20_000,
            "output_tokens": 800,
            "reasoning_tokens": 2_000,
            "cache_read_tokens": 5_000,
            "tool_calls": 8,
            "mutation_tool_calls": 2,
            "patch_parts": 1,
        },
    )
    monkeypatch.setattr(launch_kilo_exact, "_has_worktree_progress", lambda *_a, **_k: True)
    code, reason, metrics = launch_kilo_exact._wait_with_budgets(
        FakeProc(),
        db_path=tmp_path / "db",
        session_id="ses",
        worktree=tmp_path,
        launch_git={"is_git": False},
        max_wall_seconds=600,
        max_input_tokens=500_000,
        max_reasoning_tokens=200_000,
        max_tool_calls=80,
        max_no_progress_input_tokens=75_000,
        poll_seconds=0.01,
    )
    assert code == 0
    assert reason is None
    assert metrics["worktree_progress"] is True
