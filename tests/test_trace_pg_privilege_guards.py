"""Offline gate for server-enforced admission and separate SCRAM roles.

Never touches a database, Docker process, remote node, secret, or network.
"""
from copy import deepcopy
from pathlib import Path

import pytest
import probe_trace_pg_privileged_roles as probe


def fixture():
    return ({
        "Name":"/"+probe.CONTAINER,
        "State":{"Running":True},
        "Config":{"Image":"postgres:17-alpine",
                  "Env":["POSTGRES_HOST_AUTH_METHOD=scram-sha-256"]},
        "HostConfig":{"NetworkMode":probe.NETWORK,"NanoCpus":1_000_000_000,
                      "Memory":536_870_912,"PortBindings":{}},
        "NetworkSettings":{"Networks":{probe.NETWORK:{"IPAddress":"172.23.0.2"}}},
        "Mounts":[{"Type":"tmpfs"}],
    }, {"Internal":True})


def test_probe_requires_explicit_opt_in_without_touching_docker(monkeypatch):
    monkeypatch.delenv("ASSISTX_TRACE_PG_PRIVILEGE_EXPERIMENT", raising=False)
    monkeypatch.setattr(probe,"_inspect",
        lambda *_: (_ for _ in ()).throw(AssertionError("no Docker access")))
    with pytest.raises(RuntimeError, match="EXPLICIT_OPT_IN_REQUIRED"):
        probe._address()


def test_scram_fixture_requires_exact_identity_and_no_host_ports(monkeypatch):
    monkeypatch.setenv("ASSISTX_TRACE_PG_PRIVILEGE_EXPERIMENT","1")
    c,n=fixture()
    monkeypatch.setattr(probe,"_inspect",
        lambda command,*a:[c] if command=="inspect" else [n])
    assert probe._address()=="172.23.0.2"


@pytest.mark.parametrize("mutator",[
    lambda c,n:c.update(Name="/foreign"),
    lambda c,n:c["State"].update(Running=False),
    lambda c,n:c["Config"].update(Image="postgres:latest"),
    lambda c,n:c["Config"].update(Env=["POSTGRES_HOST_AUTH_METHOD=trust"]),
    lambda c,n:c["HostConfig"].update(NetworkMode="host"),
    lambda c,n:c["HostConfig"].update(PortBindings={"5432/tcp":[{"HostPort":"5432"}]}),
    lambda c,n:c["NetworkSettings"]["Networks"].update(
        bridge={"IPAddress":"172.17.0.4"}),
    lambda c,n:n.update(Internal=False),
    lambda c,n:c["Mounts"].append({"Type":"bind"}),
    lambda c,n:c["HostConfig"].update(Memory=0),
    lambda c,n:c["HostConfig"].update(Memory=2_000_000_000),
    lambda c,n:c["HostConfig"].update(NanoCpus=2_000_000_000),
    lambda c,n:c["NetworkSettings"]["Networks"][probe.NETWORK].update(
        IPAddress="10.0.0.5"),
])
def test_unsafe_auth_fixture_rejected(monkeypatch,mutator):
    monkeypatch.setenv("ASSISTX_TRACE_PG_PRIVILEGE_EXPERIMENT","1")
    c,n=deepcopy(fixture())
    mutator(c,n)
    monkeypatch.setattr(probe,"_inspect",
        lambda command,*a:[c] if command=="inspect" else [n])
    with pytest.raises(RuntimeError,match="UNSAFE_FIXTURE"):
        probe._address()


def test_sql_enforces_atomic_capacity_and_default_privileges_offline():
    sql=(Path(probe.__file__).parents[1]/"research"/
         "trace_pg_privilege_fence.sql").read_text()
    assert "FOR UPDATE;" in sql
    assert "SECURITY DEFINER SET search_path = pg_catalog, pg_temp" in sql
    assert "REVOKE ALL ON ALL FUNCTIONS" in sql
    assert "REVOKE ALL ON ALL TABLES" in sql
    assert "schema_version = 'assistx-pg-privilege-v1'" in sql
    assert "CREATE FUNCTION assistx_trace_fence_research.release_exact" in sql
    assert "CREATE FUNCTION assistx_trace_fence_research.admit" in sql


def test_test_harness_grants_only_worker_admit_and_verifier_release():
    source=Path(probe.__file__).read_text()
    assert 'sql.Identifier(WORKER)' in source
    assert 'sql.Identifier(VERIFIER)' in source
    assert 'GRANT EXECUTE ON FUNCTION {}.admit' in source
    assert 'GRANT EXECUTE ON FUNCTION {}.inspect(text), ' in source
    assert '{}.release_exact(text,text,text) TO {}' in source
    assert 'db.commit()' in source  # no metadata lock across pg_sleep
