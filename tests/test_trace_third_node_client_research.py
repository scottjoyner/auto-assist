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


def test_shell_significant_inputs_rejected_before_ssh(monkeypatch):
    monkeypatch.setenv("ASSISTX_THREEHOST_RESEARCH_ONLY","yes-disposable")
    monkeypatch.setattr(client.socket,"gethostname",lambda:"xwing")
    monkeypatch.setattr(client.subprocess,"run",lambda *a,**k:
        (_ for _ in ()).throw(AssertionError("unsafe SSH execution")))
    for name in ("synthetic-a;id","synthetic-a $(id)","synthetic-a\nfoo"):
        assert client.submit(name,EPOCH)["status"]=="invalid-research-request"
    assert client.submit("synthetic-good","bad;id")["status"]=="invalid-research-request"


def test_same_host_unbound_admit_response_is_rejected(monkeypatch):
    import json
    monkeypatch.setenv("ASSISTX_THREEHOST_RESEARCH_ONLY","yes-disposable")
    monkeypatch.setattr(client.socket,"gethostname",lambda:"x1-370")
    for field,value in (("query_ref","synthetic-other"),
                        ("epoch","f4bd38be-3434-4b43-94f1-45cf1dbb7bb6"),
                        ("graph_id","b"*64)):
        fake={"status":"admitted","host":"raspberrypi","sequence":1,"active":1,
              "epoch":EPOCH,"graph_id":"a"*64,"query_ref":"synthetic-good",
              "token":"a"*32,"receiver_nonce":"a73cb6d6-04ba-4fb3-b92c-0a28f0dbbb9c"}
        fake[field]=value
        monkeypatch.setattr(client.subprocess,"run",
            lambda *a,**k:Result(0,json.dumps(fake)))
        assert client.submit("synthetic-good",EPOCH)["status"]=="untrusted-authority-response"


def test_valid_bound_synthetic_admission_roundtrip(monkeypatch):
    import json
    monkeypatch.setenv("ASSISTX_THREEHOST_RESEARCH_ONLY","yes-disposable")
    monkeypatch.setattr(client.socket,"gethostname",lambda:"xwing")
    fake={"status":"admitted","host":"raspberrypi","sequence":1,"active":1,
          "epoch":EPOCH,"graph_id":"a"*64,"query_ref":"synthetic-xwing",
          "token":"a"*32,"receiver_nonce":"a73cb6d6-04ba-4fb3-b92c-0a28f0dbbb9c"}
    monkeypatch.setattr(client.subprocess,"run",
        lambda *a,**k:Result(0,json.dumps(fake)))
    assert client.submit("synthetic-xwing",EPOCH)["status"]=="admitted"
