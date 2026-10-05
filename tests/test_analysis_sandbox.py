import pytest

from assistx.agents.analyst import _sandbox_command, run_user_code


def test_analysis_sandbox_allows_basic_computation():
    code = """
def main(rows):
    total = sum(r.get("n", 0) for r in rows)
    print("computed", total)
    return {"total": total}
"""
    result, stdout = run_user_code(code, [{"n": 2}, {"n": 3}])
    assert result == {"total": 5}
    assert "computed 5" in stdout


def test_analysis_sandbox_blocks_file_io():
    code = """
def main(rows):
    with open("/tmp/should_not_write.txt", "w") as f:
        f.write("x")
    return {"ok": True}
"""
    with pytest.raises(RuntimeError) as exc:
        run_user_code(code, [])
    assert "file I/O disabled" in str(exc.value)


def test_analysis_sandbox_blocks_network_socket():
    code = """
def main(rows):
    import socket
    s = socket.socket()
    return {"ok": True}
"""
    with pytest.raises(RuntimeError) as exc:
        run_user_code(code, [])
    msg = str(exc.value)
    assert "Import not allowed: socket" in msg or "network disabled" in msg


def test_analysis_sandbox_timeout(monkeypatch):
    monkeypatch.setenv("ANALYSIS_TIMEOUT_S", "1")
    code = """
def main(rows):
    while True:
        pass
"""
    with pytest.raises(RuntimeError) as exc:
        run_user_code(code, [])
    assert "timed out" in str(exc.value).lower() or "sandbox failed" in str(exc.value).lower()


def test_analysis_sandbox_executes_runner_without_importing_assistx_package():
    cmd = _sandbox_command()
    assert cmd[0]
    assert cmd[-1].endswith("/assistx/sandbox_runner.py")
    assert "-m" not in cmd


def test_analysis_sandbox_small_incremental_memory_budget_still_allows_basic_code(monkeypatch):
    monkeypatch.setenv("ANALYSIS_MEM_MB", "64")
    result, stdout = run_user_code(
        "def main(rows):\n    return {\"count\": len(rows)}\n",
        [{"n": 1}],
    )
    assert result == {"count": 1}
    assert stdout == ""
