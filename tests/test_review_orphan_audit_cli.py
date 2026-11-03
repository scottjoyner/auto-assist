"""No network/no production credentials: safe CLI contracts."""
from __future__ import annotations

import json
from pathlib import Path
import runpy

import pytest


tool=runpy.run_path(str(
    Path(__file__).resolve().parents[1] / "scripts/audit_review_orphans.py"
))
main=tool["main"]


def test_never_runs_without_explicit_readonly_flag():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code==2


@pytest.mark.parametrize("value",["0","301","-3"])
def test_limit_enforced_before_connection(value):
    with pytest.raises(SystemExit) as exc:
        main(["--read-only-audit","--limit",value])
    assert exc.value.code==2


def test_missing_credentials_never_prints_host_or_secret(monkeypatch,capsys):
    for name in ("NEO4J_URI","NEO4J_USER","NEO4J_PASSWORD"):
        monkeypatch.delenv(name,raising=False)
    assert main(["--read-only-audit"])==2
    reported=json.loads(capsys.readouterr().out)
    assert reported=={
        "decision":"HOLD","error_type":"missing_connection_configuration",
    }


def test_backend_exception_redacted(monkeypatch,capsys):
    for name in ("NEO4J_URI","NEO4J_USER","NEO4J_PASSWORD"):
        monkeypatch.setenv(name,"sensitive-credential")
    class BrokenDB:
        @staticmethod
        def driver(*args,**kwargs):
            raise RuntimeError("secret='sensitive-credential' or localhost URI")
    monkeypatch.setitem(main.__globals__,"GraphDatabase",BrokenDB)
    assert main(["--read-only-audit"])==2
    value=capsys.readouterr().out
    assert "sensitive-credential" not in value
    assert "localhost" not in value
    assert json.loads(value)=={"decision":"HOLD","error_type":"RuntimeError"}
