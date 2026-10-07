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


def test_qualified_models_are_exact_and_live_ids():
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
        "stepfun/step-3.7-flash:free",
    }
    assert launch_kilo_exact.QUALIFIED_EXACT_MODELS == expected
    for model in expected:
        assert launch_kilo_exact.route_kind(model) == "exact"
        launch_kilo_exact.validate_model(model)


def test_alias_and_unknown_models_fail_closed():
    for model in ("openrouter/free", "kilo/auto", "auto", "free"):
        try:
            launch_kilo_exact.validate_model(model)
            raise AssertionError(model)
        except ValueError:
            pass
    try:
        launch_kilo_exact.validate_model("some-paid/model")
        raise AssertionError("unknown model should fail")
    except ValueError as exc:
        assert "qualified" in str(exc)


def test_build_command_matches_proven_live_cli(monkeypatch):
    monkeypatch.setenv("OPENCODE_BIN", "/opt/opencode")
    worktree = pathlib.Path("/tmp/trace-worktree")
    cmd = launch_kilo_exact.build_command(
        "cohere/north-mini-code:free",
        "trace-title",
        "do useful work",
        worktree,
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
    assert launch_kilo_exact.classify_failure(
        "maximum context length is 65536 tokens; requested 66614 tokens"
    ) == "context_length_exceeded"
    assert launch_kilo_exact.classify_failure("The operation timed out") == "timeout"
    assert launch_kilo_exact.classify_failure("boom") == "process_error"


def test_run_failure_classification_ignores_tool_output_keywords():
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "state": {
                            "output": "source mentions HTTP 429, quota, and rate-limited"
                        }
                    },
                }
            ),
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "data": {
                            "message": (
                                "This endpoint's maximum context length is 65536 tokens. "
                                "However, you requested about 66614 tokens."
                            )
                        }
                    },
                }
            ),
        ]
    )
    assert launch_kilo_exact.classify_run_failure("", stdout) == "context_length_exceeded"


def test_run_failure_classification_prefers_real_timeout_event():
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {"state": {"output": "429 rate-limited"}},
                }
            ),
            json.dumps(
                {
                    "type": "error",
                    "error": {"data": {"message": "The operation timed out."}},
                }
            ),
        ]
    )
    assert launch_kilo_exact.classify_run_failure("", stdout) == "timeout"


def test_dry_run_does_not_spawn_process(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_BIN", "/opt/opencode")
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
        def wait(self, timeout=None):
            return 0
        def terminate(self):
            pass
        def kill(self):
            pass

    fake_proc = FakeProc()
    monkeypatch.setenv("OPENCODE_BIN", "/opt/opencode")
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

    kwargs = popen.call_args.kwargs
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


def test_raw_error_summary_ignores_tool_output_failure_words(tmp_path):
    trace = tmp_path / "run.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "tool_use",
                        "part": {"state": {"output": "429 quota rate-limited"}},
                    }
                ),
                json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "data": {
                                "message": (
                                    "maximum context length is 65536 tokens; "
                                    "requested 66614 tokens"
                                )
                            }
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    summary = launch_kilo_exact._raw_error_summary(trace)
    assert summary == {
        "count": 1,
        "classes": {"context_length_exceeded": 1},
        "status_codes": {},
    }


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


def test_final_session_trace_filters_exporter_records_by_session_id(tmp_path):
    exporter = tmp_path / "exporter.py"
    exporter.write_text(
        "def export_sessions(db_path, session_id=None):\n"
        "    records = [\n"
        "        {'session_id': 'ses-other', 'cost': 0},\n"
        "        {'session_id': 'ses-target', 'cost': 0},\n"
        "    ]\n"
        "    return [r for r in records if session_id is None or r['session_id'] == session_id]\n"
    )
    record = launch_kilo_exact._final_session_trace(
        exporter,
        tmp_path / "unused.db",
        "ses-target",
    )
    assert record == {"session_id": "ses-target", "cost": 0}
