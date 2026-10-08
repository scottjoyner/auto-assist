import assistx.agents.hermes_agent_adapter as adapter


def test_run_hermes_passes_resume_session_to_cli(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = "ok\nsession_id: resumed-session\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    result = adapter.run_hermes(
        "follow-up",
        provider="assistx-router",
        resume_session_id="resumed-session",
    )

    resume_index = captured["cmd"].index("--resume")
    assert captured["cmd"][resume_index + 1] == "resumed-session"
    assert result["session_id"] == "resumed-session"
