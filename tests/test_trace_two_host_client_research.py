"""SSH client refuses network/authority uncertainty; never uses a local copy."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import probe_trace_two_host_client as client


PATH="/tmp/assistx-twohost-authority-test-20261009-synthetic/authority-test.sqlite"
EPOCH="9db4f73e-9274-4b8c-8f93-09a632b2695c"


class Result:
    def __init__(self,returncode,stdout=""):
        self.returncode=returncode
        self.stdout=stdout


def test_transport_failure_never_tries_fallback_copy(monkeypatch):
    monkeypatch.setenv("ASSISTX_TWOHOST_RESEARCH_ONLY","yes-disposable")
    sent=[]
    def fake_run(cmd,**kwargs):
        sent.append(cmd)
        return Result(255)
    monkeypatch.setattr(client.subprocess,"run",fake_run)
    result=client.run_one("synthetic-xwing-1",path=PATH,epoch=EPOCH,
                          fail_transport=True)
    assert result["status"]=="authority-unavailable"
    assert len(sent)==1
    assert sent[0][0]=="ssh"
    assert "ProxyCommand=/bin/false" in sent[0]
    assert all("bootstrap" not in s for s in sent[0])


def test_untrusted_host_response_cannot_become_admission(monkeypatch):
    monkeypatch.setenv("ASSISTX_TWOHOST_RESEARCH_ONLY","yes-disposable")
    monkeypatch.setattr(client.subprocess,"run",
        lambda *a,**k:Result(0,'{"host":"other-host","status":"admitted"}'))
    response=client.run_one("synthetic-xwing-2",path=PATH,epoch=EPOCH)
    assert response["status"]=="untrusted-authority-response"


def test_absent_opt_in_and_production_path_refused_before_command(monkeypatch):
    monkeypatch.delenv("ASSISTX_TWOHOST_RESEARCH_ONLY",raising=False)
    def unreachable(*a,**k):
        raise AssertionError("ssh should not execute")
    monkeypatch.setattr(client.subprocess,"run",unreachable)
    assert client.run_one("synthetic-xwing",path=PATH,epoch=EPOCH)["status"]=="invalid-research-request"
    monkeypatch.setenv("ASSISTX_TWOHOST_RESEARCH_ONLY","yes-disposable")
    assert client.run_one("synthetic-xwing",path="/nas/prod.db",epoch=EPOCH)["status"]=="invalid-research-request"


def test_local_execution_on_non_authority_host_denied(monkeypatch):
    monkeypatch.setenv("ASSISTX_TWOHOST_RESEARCH_ONLY","yes-disposable")
    monkeypatch.setattr(client.socket,"gethostname",lambda:"xwing")
    response=client.run_one("synthetic-xwing",path=PATH,epoch=EPOCH,
                            owner_local=True)
    assert response["status"]=="unsafe-local-owner"


def test_ssh_connection_timeout_returns_unavailable_not_admitted(monkeypatch):
    monkeypatch.setenv("ASSISTX_TWOHOST_RESEARCH_ONLY","yes-disposable")
    def failure(*args,**kwargs):
        raise client.subprocess.TimeoutExpired("ssh",3)
    monkeypatch.setattr(client.subprocess,"run",failure)
    response=client.run_one("synthetic-xwing",path=PATH,epoch=EPOCH)
    assert response["status"]=="authority-unavailable"
    assert response["attempted_ssh"] is True
