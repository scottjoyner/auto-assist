"""No SSH/Docker in CI tests: deliberately limited operator-control evidence."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit-disposable-fleet-admin-boundary.py"
spec = importlib.util.spec_from_file_location("fleet_admin_boundary_denyonly", SCRIPT)
assert spec and spec.loader
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)

GOOD = "MARKER=FLEET_READ_ONLY_AUTHORITY_V1\nUID=1000\nUSER=synthetic\nDOCKER_ACCESS=1\n"
NO_DOCKER = GOOD.replace("DOCKER_ACCESS=1", "DOCKER_ACCESS=0")


def fake_runner(mapping):
    recorded = []

    def run(args, **opts):
        recorded.append(args)
        assert args[0] == "ssh"
        assert ("StrictHostKeyChecking=yes" in args
                and "BatchMode=yes" in args
                and "NumberOfPasswordPrompts=0" in args)
        assert opts["timeout"] <= 10 and opts["capture_output"] and opts["text"]
        assert opts["check"] is False
        alias = args[-2]
        out = mapping.get(alias)
        if isinstance(out, Exception):
            raise out
        if out is None:
            return SimpleNamespace(returncode=255, stdout="", stderr="synthetic")
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    return recorded, run


def test_same_operator_docker_on_two_hosts_is_no_go():
    records, runner = fake_runner({"nodeA": GOOD, "nodeB": GOOD})
    result = lab.audit(["nodeA", "nodeB"], runner=runner)
    assert result["verdict"] == "NO_GO_SHARED_OPERATOR_DOCKER_REACHABILITY"
    assert all(n["docker_admin_accessible"] for n in result["nodes"])
    assert len(records) == 2
    assert "synthetic" not in json.dumps(result)  # no remote usernames disclosed


def test_one_host_accessible_other_unknown_must_never_be_go():
    records, runner = fake_runner({"nodeA": GOOD})
    result = lab.audit(["nodeA", "nodeB"], runner=runner)
    assert result["verdict"] == "UNKNOWN_NO_RELEASE_AUTHORITY"
    assert result["nodes"][1]["operator_reachable"] is None


def test_no_docker_at_second_host_still_unknown_not_authorized():
    records, runner = fake_runner({"nodeA": GOOD, "nodeB": NO_DOCKER})
    result = lab.audit(["nodeA", "nodeB"], runner=runner)
    assert result["verdict"] == "UNKNOWN_NO_RELEASE_AUTHORITY"


@pytest.mark.parametrize("input_aliases", [
    [], ["xwing", "xwing"], ["node one"], ["-oStrictHostKeyChecking=no"],
    ["../sensitive"], ["node;command"], ["x" * 65], ["host"] * 7,
])
def test_user_aliases_cannot_become_shell_or_option_injection(input_aliases):
    def explode(*_args, **_kwargs):
        pytest.fail("invalid aliases must not call SSH")
    assert lab.audit(input_aliases, runner=explode)["reason"] == "invalid_alias_scope"


@pytest.mark.parametrize("stdout", [
    GOOD + "USER=second\n",
    GOOD.replace("DOCKER_ACCESS=1", "DOCKER_ACCESS=True"),
    GOOD.replace("UID=1000", "UID=oops"),
    GOOD.replace("MARKER=FLEET_READ_ONLY_AUTHORITY_V1", "MARKER=OTHER"),
    GOOD + "SECRET=synthetic\n",
    GOOD + "x" * 500,
])
def test_malformed_remote_output_yields_unknown_not_go(stdout):
    _, runner = fake_runner({"nodeA": stdout, "nodeB": GOOD})
    result = lab.audit(["nodeA", "nodeB"], runner=runner)
    assert result["verdict"] == "UNKNOWN_NO_RELEASE_AUTHORITY"


def test_unreachable_or_timedout_host_is_unknown_no_fallback():
    _, runner = fake_runner({"nodeA": GOOD, "nodeB": subprocess.TimeoutExpired("ssh", 10)})
    assert lab.audit(["nodeA", "nodeB"], runner=runner)["verdict"] == "UNKNOWN_NO_RELEASE_AUTHORITY"


def test_cli_never_exits_success_even_with_fully_reachable_fixtures(monkeypatch, capsys):
    monkeypatch.setattr(lab, "audit", lambda aliases: {
        "schema_version": 1, "verdict": "NO_GO_SHARED_OPERATOR_DOCKER_REACHABILITY",
        "nodes": [], "reason": "synthetic",
    })
    assert lab.main(["nodeA", "nodeB"]) != 0
    assert '"NO_GO_SHARED_OPERATOR_DOCKER_REACHABILITY"' in capsys.readouterr().out
    monkeypatch.setattr(lab, "audit", lambda aliases: {
        "schema_version": 1, "verdict": "UNKNOWN_NO_RELEASE_AUTHORITY",
        "nodes": [], "reason": "unverified",
    })
    assert lab.main(["nodeA", "nodeB"]) != 0
