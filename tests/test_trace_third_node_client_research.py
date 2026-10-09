"""No real SSH: research Pi client can only use pinned remote owner."""
import importlib.util
from pathlib import Path
import sys

import pytest

f=Path(__file__).with_name("probe_trace_third_node_client.py")
spec=importlib.util.spec_from_file_location("three_host_research_client",f)
client=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=client
spec.loader.exec_module(client)
EPOCH="f8d7c2b1-7f44-4cd5-bba8-3325b9a4b073"


class Result:
    def __init__(self,ret=0,out=""):
        self.returncode=ret
        self.stdout=out


def test_transport_failure_denies_without_fallback(monkeypatch):
    monkeypatch.setenv("ASSISTX_THREEHOST_RESEARCH_ONLY","yes-disposable")
    monkeypatch.setattr(client.socket,"gethostname",lambda:"xwing")
    calls=[]
    def run(cmd,**kw):
        calls.append(cmd)
        return Result(255)
    monkeypatch.setattr(client.subprocess,"run",run)
    answer=client.submit("synthetic-xwing-1",EPOCH,fail_ssh=True)
    assert answer["status"]=="authority-unavailable"
    assert len(calls)==1
    assert calls[0][0]=="ssh" and "raspberrypi" in calls[0]
    assert "ProxyCommand=/bin/false" in calls[0]
    assert all("bootstrap" not in s for s in calls[0])


def test_unexpected_host_response_never_grants(monkeypatch):
    monkeypatch.setenv("ASSISTX_THREEHOST_RESEARCH_ONLY","yes-disposable")
    monkeypatch.setattr(client.socket,"gethostname",lambda:"x1-370")
    monkeypatch.setattr(client.subprocess,"run",
        lambda *a,**k:Result(0,'{"host":"xwing","status":"admitted"}'))
    assert client.submit("synthetic-a",EPOCH)["status"]=="untrusted-authority-response"


def test_optin_and_host_required_before_any_ssh(monkeypatch):
    monkeypatch.delenv("ASSISTX_THREEHOST_RESEARCH_ONLY",raising=False)
    monkeypatch.setattr(client.socket,"gethostname",lambda:"x1-370")
    monkeypatch.setattr(client.subprocess,"run",
        lambda *a,**k:(_ for _ in ()).throw(AssertionError("SSH not permitted")))
    assert client.submit("synthetic-a",EPOCH)["status"]=="invalid-research-request"
    monkeypatch.setenv("ASSISTX_THREEHOST_RESEARCH_ONLY","yes-disposable")
    monkeypatch.setattr(client.socket,"gethostname",lambda:"unauthorized-host")
    assert client.submit("synthetic-a",EPOCH)["status"]=="invalid-research-request"


def test_ssh_timeout_denies_not_admits(monkeypatch):
    monkeypatch.setenv("ASSISTX_THREEHOST_RESEARCH_ONLY","yes-disposable")
    monkeypatch.setattr(client.socket,"gethostname",lambda:"xwing")
    def timeout(*a,**k):
        raise client.subprocess.TimeoutExpired("ssh",13)
    monkeypatch.setattr(client.subprocess,"run",timeout)
    assert client.submit("synthetic-a",EPOCH)["status"]=="authority-unavailable"
