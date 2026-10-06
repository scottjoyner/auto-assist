import hashlib
from pathlib import Path
import sys

from assistx.rtk_adapter import RTKAdapter


def _fake_rtk(tmp_path: Path, *, fail_pipe: bool = False) -> Path:
    binary = tmp_path / "rtk"
    code = 7 if fail_pipe else 0
    binary.write_text(
        f'''#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "rtk 0.51.0"
  exit 0
fi
if [ "$1" = "pipe" ]; then
  if [ {code} -ne 0 ]; then exit {code}; fi
  tr '[:lower:]' '[:upper:]'
  exit 0
fi
exit 2
'''
    )
    binary.chmod(0o755)
    return binary


def _single_run_script(tmp_path: Path) -> Path:
    script = tmp_path / "emit.py"
    script.write_text(
        '''from pathlib import Path
counter = Path("counter.txt")
count = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(count + 1))
print("hello world")
'''
    )
    return script


def test_rtk_compacts_after_single_execution_and_preserves_raw(tmp_path):
    binary = _fake_rtk(tmp_path)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    script = _single_run_script(tmp_path)
    result = RTKAdapter(
        str(binary), expected_version="0.51.0", expected_sha256=digest
    ).run_command(
        [sys.executable, str(script)], cwd=str(tmp_path),
        evidence_dir=str(tmp_path / "evidence"), filter_name="pytest",
    )

    assert (tmp_path / "counter.txt").read_text() == "1"
    assert result.compaction_status == "compacted"
    assert Path(result.raw_stdout_path).read_bytes() == b"hello world\n"
    assert Path(result.compact_stdout_path).read_bytes() == b"HELLO WORLD\n"
    assert result.raw_stdout_sha256 == hashlib.sha256(b"hello world\n").hexdigest()


def test_rtk_failure_falls_back_to_raw_without_rerunning_command(tmp_path):
    binary = _fake_rtk(tmp_path, fail_pipe=True)
    script = _single_run_script(tmp_path)
    result = RTKAdapter(str(binary), expected_version="0.51.0").run_command(
        [sys.executable, str(script)], cwd=str(tmp_path),
        evidence_dir=str(tmp_path / "evidence"), filter_name="pytest",
    )

    assert (tmp_path / "counter.txt").read_text() == "1"
    assert result.compaction_status == "fallback:rtk-exit-7"
    assert Path(result.compact_stdout_path).read_bytes() == b"hello world\n"


def test_missing_rtk_still_returns_raw_model_output(tmp_path):
    script = _single_run_script(tmp_path)
    result = RTKAdapter(str(tmp_path / "missing-rtk")).run_command(
        [sys.executable, str(script)], cwd=str(tmp_path),
        evidence_dir=str(tmp_path / "evidence"), filter_name="pytest",
    )
    assert (tmp_path / "counter.txt").read_text() == "1"
    assert result.compaction_status == "fallback:rtk-unavailable"
    assert Path(result.model_output_path).read_bytes() == b"hello world\n"


def test_command_timeout_still_persists_partial_raw_output(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text(
        'import sys, time\nprint("before timeout", flush=True)\nprint("err line", file=sys.stderr, flush=True)\ntime.sleep(2)\n'
    )
    result = RTKAdapter(str(tmp_path / "missing-rtk"), timeout_seconds=1).run_command(
        [sys.executable, str(script)], cwd=str(tmp_path),
        evidence_dir=str(tmp_path / "evidence"), filter_name=None,
    )
    assert result.exit_code == 124
    assert b"before timeout" in Path(result.raw_stdout_path).read_bytes()
    assert b"err line" in Path(result.raw_stderr_path).read_bytes()
    assert Path(result.model_output_path).read_bytes() == Path(result.raw_stdout_path).read_bytes()
