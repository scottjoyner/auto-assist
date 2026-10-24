"""Local kernel peer credential and fixed-command boundary research tests."""
from __future__ import annotations

import json
import os
import socket
import struct
from pathlib import Path

import pytest

from assistx import hermes_broker_unix_boundary_research as boundary
from assistx.hermes_broker_command_research import CandidateBrokerCommandError


def complete_command():
    return {"operation":"complete","expected_generation":2,
            "task_id":"synthetic-task","claim_id":"synthetic-claim",
            "session_id":"synthetic-session","attempt":1}

def wire(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":")).encode()+b"\n"

def synthetic_identity(monkeypatch,allowed_uid):
    monkeypatch.setattr(boundary,"_peer_credentials",lambda sock:(os.getpid(),allowed_uid,os.getgid()))

class Counter:
    def __init__(self):
        self.calls=0
    def __call__(self):
        self.calls+=1
        raise AssertionError("unexpected privileged DB session")


def _request(raw,monkeypatch,*,valid_peer=True):
    allow=os.geteuid()+1000
    if valid_peer:
        synthetic_identity(monkeypatch,allow)
    a,b=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
    try:
        if raw is not None:
            b.sendall(raw)
        count=Counter()
        return boundary.serve_one_candidate_connection(
            a,allowed_peer_uid=allow,session_factory=count),count
    finally:
        a.close();b.close()

def test_real_kernel_peercred_readback_and_same_uid_rejected_without_io():
    a,b=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
    try:
        pid,uid,gid=boundary._peer_credentials(a)
        assert pid>0 and uid==os.geteuid() and gid==os.getegid()
        counter=Counter()
        # Exact host UID cannot be treated as independently isolated.
        with pytest.raises(boundary.CandidateLocalTransportError,match="unsafe_peer_uid_configuration"):
            boundary.serve_one_candidate_connection(
                a,allowed_peer_uid=os.geteuid(),session_factory=counter)
        assert counter.calls==0
        with pytest.raises(boundary.CandidateLocalTransportError,match="peer_not_authorized"):
            boundary.serve_one_candidate_connection(
                a,allowed_peer_uid=os.geteuid()+1000,session_factory=counter)
        assert counter.calls==0
    finally:
        a.close();b.close()

@pytest.mark.parametrize("uid",[None,True,-1,"1000",os.geteuid()])
def test_unsafe_uid_configuration_fails_before_peer_or_session(uid,monkeypatch):
    calls=[]
    monkeypatch.setattr(boundary,"_peer_credentials",lambda sock:calls.append(True))
    a,b=socket.socketpair()
    try:
        with pytest.raises(boundary.CandidateLocalTransportError,match="unsafe_peer_uid_configuration"):
            boundary.serve_one_candidate_connection(
                a,allowed_peer_uid=uid,session_factory=Counter())
        assert calls==[]
    finally:
        a.close();b.close()

@pytest.mark.parametrize("raw",[
    b"", b"\n", b"not-json\n", b"{\n",
    b'{"version":1,"version":1,"command":{}}\n',
    b'{"version":1,"command":{},"cypher":"query"}\n',
    b'{"version":true,"command":{}}\n',
    b'{"version":1,"command":[] }\n',
    b'{"version":1,"command":{},"number":NaN}\n',
    b"\xff\xff\n",
    b'{"version":1,"command":{}}\n{"version":1}\n',
    b"a"*1025+b"\n",
])
def test_bad_frames_fail_closed_before_database(raw,monkeypatch):
    if not raw:
        # EOF instead of waiting for the deadline.
        a,b=socket.socketpair()
        try:
            synthetic_identity(monkeypatch,os.geteuid()+1000)
            b.shutdown(socket.SHUT_WR)
            counter=Counter()
            with pytest.raises(boundary.CandidateLocalTransportError):
                boundary.serve_one_candidate_connection(
                    a,allowed_peer_uid=os.geteuid()+1000,session_factory=counter)
            assert counter.calls==0
        finally:
            a.close();b.close()
    else:
        with pytest.raises(boundary.CandidateLocalTransportError):
            _request(raw,monkeypatch)

def test_missing_frame_times_out_with_bounded_deadline(monkeypatch):
    synthetic_identity(monkeypatch,os.geteuid()+1000)
    monkeypatch.setattr(boundary,"MAX_DURATION_SECONDS",0.02)
    a,b=socket.socketpair()
    try:
        with pytest.raises(boundary.CandidateLocalTransportError):
            boundary.serve_one_candidate_connection(
                a,allowed_peer_uid=os.geteuid()+1000,
                session_factory=Counter())
    finally:
        a.close();b.close()

def test_valid_peer_calls_only_fixed_command_and_preserves_hold(monkeypatch):
    synthetic_identity(monkeypatch,os.geteuid()+1000)
    seen=[]
    def fake(cmd,*,trusted_channel_principal,session_factory):
        seen.append((cmd,trusted_channel_principal))
        return {"candidate_generation":3}
    monkeypatch.setattr(boundary,"dispatch_candidate_command",fake)
    a,b=socket.socketpair()
    try:
        b.sendall(wire({"version":1,"command":complete_command()}))
        result=boundary.serve_one_candidate_connection(
            a,allowed_peer_uid=os.geteuid()+1000,
            session_factory=Counter())
        assert len(seen)==1
        assert seen[0][0]==complete_command()
        assert seen[0][1]=="hermes-local"
        assert result["candidate_generation"]==3
        assert result["transport_kernel_peer_uid_matched"]
        for name in ("transport_fleet_authenticated",
                     "broker_exclusive_credentials_verified",
                     "all_writers_generation_fenced",
                     "independent_revocation_custody_verified",
                     "external_effects_verified",
                     "production_dispatch_authorized"):
            assert result[name] is False
        assert result["decision"]=="HOLD"
    finally:
        a.close();b.close()

def test_fixed_command_rejection_is_redacted(monkeypatch):
    synthetic_identity(monkeypatch,os.geteuid()+1000)
    def fail(*args,**kw):
        raise CandidateBrokerCommandError("synthetic_secret_Task_id")
    monkeypatch.setattr(boundary,"dispatch_candidate_command",fail)
    a,b=socket.socketpair()
    try:
        b.sendall(wire({"version":1,"command":complete_command()}))
        with pytest.raises(boundary.CandidateLocalTransportError) as info:
            boundary.serve_one_candidate_connection(
                a,allowed_peer_uid=os.geteuid()+1000,session_factory=Counter())
        assert "synthetic_secret" not in str(info.value)
        assert "command_rejected" in str(info.value)
    finally:
        a.close();b.close()

def test_production_modules_do_not_import_or_start_local_research_transport():
    base=Path(__file__).resolve().parents[1]/"src/assistx"
    for file in base.rglob("*.py"):
        if file.name.startswith("hermes_broker_") or file.name.startswith("hermes_source_"):
            continue
        assert "hermes_broker_unix_boundary_research" not in file.read_text(),file
