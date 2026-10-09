"""Offline security tests for opt-in shared PostgreSQL research authority.

No Docker, production account, Postgres data or provider interaction.
"""
from copy import deepcopy
import os
import uuid
import pytest

from assistx.trace_pg_authority_research import (
    NAME, NETWORK, Admission, PostgresTraceAuthority,
    _valid_epoch, checked_disposable_dsn,
)

def fixture():
    return ({
        "Name": "/" + NAME,
        "State":{"Running":True},
        "Config":{"Image":"postgres:17-alpine","Env":["POSTGRES_HOST_AUTH_METHOD=trust"]},
        "HostConfig":{"NetworkMode":NETWORK,"NanoCpus":1_000_000_000,
                      "Memory":536_870_912,"PortBindings":{}},
        "NetworkSettings":{"Networks":{NETWORK:{"IPAddress":"172.23.0.2"}}},
        "Mounts":[{"Type":"tmpfs"}],
    },{"Internal":True})


def test_disposable_authority_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("ASSISTX_TRACE_PG_DISPOSABLE_RESEARCH",raising=False)
    monkeypatch.setattr("assistx.trace_pg_authority_research._inspect",
                        lambda *_: (_ for _ in ()).throw(
                            AssertionError("should not inspect Docker")))
    with pytest.raises(RuntimeError,match="EXPLICIT_RESEARCH_OPT_IN_REQUIRED"):
        checked_disposable_dsn()


def test_valid_disposable_container_only(monkeypatch):
    monkeypatch.setenv("ASSISTX_TRACE_PG_DISPOSABLE_RESEARCH","1")
    c,n=fixture()
    monkeypatch.setattr("assistx.trace_pg_authority_research._inspect",
        lambda command,*a:[c] if command=="inspect" else [n])
    assert checked_disposable_dsn()=="postgresql://postgres@172.23.0.2:5432/postgres"


@pytest.mark.parametrize("fault",[
    ("hostname", lambda c,n: c.update(Name="/other-instance")),
    ("not-running",lambda c,n:c["State"].update(Running=False)),
    ("wrong-image",lambda c,n:c["Config"].update(Image="postgres:latest")),
    ("host-network",lambda c,n:c["HostConfig"].update(NetworkMode="host")),
    ("published-port",lambda c,n:c["HostConfig"].update(
        PortBindings={"5432/tcp":[{"HostIp":"0.0.0.0","HostPort":"5432"}]})),
    ("extra-net",lambda c,n:c["NetworkSettings"]["Networks"].update(
        bridge={"IPAddress":"172.17.0.3"})),
    ("external-net",lambda c,n:n.update(Internal=False)),
    ("bind-mount",lambda c,n:c["Mounts"].append({"Type":"bind"})),
    ("uncapped-cpu",lambda c,n:c["HostConfig"].update(NanoCpus=4_000_000_000)),
    ("uncapped-mem",lambda c,n:c["HostConfig"].update(Memory=0)),
    ("excess-mem",lambda c,n:c["HostConfig"].update(Memory=2_000_000_000)),
    ("foreign-ip",lambda c,n:c["NetworkSettings"]["Networks"][NETWORK].update(
        IPAddress="10.0.0.4")),
    ("auth-mode-unexpected",lambda c,n:c["Config"].update(Env=[])),
])
def test_foreign_or_unsafe_disposable_target_rejected(monkeypatch,fault):
    monkeypatch.setenv("ASSISTX_TRACE_PG_DISPOSABLE_RESEARCH","1")
    c,n=deepcopy(fixture())
    fault[1](c,n)
    monkeypatch.setattr("assistx.trace_pg_authority_research._inspect",
        lambda command,*a:[c] if command=="inspect" else [n])
    with pytest.raises(RuntimeError,match="UNSAFE_AUTHORITY_FIXTURE"):
        checked_disposable_dsn()


def test_pinned_uuid4_epoch_required():
    assert _valid_epoch(str(uuid.uuid4()))
    for value in ("", "not-an-epoch", None, 42, str(uuid.uuid1()), "a"*36):
        assert not _valid_epoch(value)


def test_authority_requires_external_verifier_public_key_before_network(monkeypatch):
    monkeypatch.setattr("assistx.trace_pg_authority_research.checked_disposable_dsn",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("network call not allowed")))
    for epoch,key in ((str(uuid.uuid4()),b""),("",b"x"*32),
                       (str(uuid.uuid4()),None)):
        with pytest.raises(ValueError,match="PINNED_EPOCH_AND_PUBLIC_KEY_REQUIRED"):
            PostgresTraceAuthority(epoch,key)


def test_cannot_admit_invalid_reference_or_accept_malformed_release_offline():
    obj=object.__new__(PostgresTraceAuthority)
    obj.epoch=str(uuid.uuid4())
    class RefuseNetwork:
        def verify(self,*_):raise AssertionError("must reject before signature")
    obj.verifier=RefuseNetwork()
    for ref in ("", "../escape","x"*129, None, True, 42):
        assert obj.acquire(ref)==Admission(None,None,"invalid-reference")
    for receipt,signature in [
        ({}, b""),
        (None,b""),
        ({"version":"unexpected"},b"0"*64),
    ]:
        assert obj.release_witnessed(receipt,signature) is False
