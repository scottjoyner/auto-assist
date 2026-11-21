"""Sanitized HOLD socket receipts; never confuse delivery with mutation."""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from assistx import hermes_broker_unix_boundary_research as boundary
from assistx import hermes_broker_rootless_research as listener


def decode(line: bytes):
    assert line.endswith(b"\n")
    assert len(line)<=boundary.MAX_REPLY_BYTES
    o=json.loads(line)
    assert o["decision"]=="HOLD"
    assert o["production_dispatch_authorized"] is False
    assert o["durable_delivery_verified"] is False
    assert "task_id" not in line.decode()
    assert "password" not in line.decode()
    return o


def test_success_reply_is_minimal_and_not_durable():
    a,b=socket.socketpair()
    try:
        boundary.send_holding_reply(a,admitted=True,candidate_generation=31)
        o=decode(b.recv(512))
        assert o=={
            "schema":1,"accepted_source_transaction":True,
            "candidate_generation":31,"durable_delivery_verified":False,
            "production_dispatch_authorized":False,"decision":"HOLD",
        }
    finally:
        a.close()
        b.close()


def test_rejection_reply_hides_claim_and_generation():
    a,b=socket.socketpair()
    try:
        boundary.send_holding_reply(a,admitted=False)
        o=decode(b.recv(512))
        assert o=={
            "schema":1,"accepted_source_transaction":False,
            "error":"request_denied","durable_delivery_verified":False,
            "production_dispatch_authorized":False,"decision":"HOLD",
        }
        assert "candidate_generation" not in o
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("kwargs",[
    {"admitted":True},
    {"admitted":True,"candidate_generation":True},
    {"admitted":True,"candidate_generation":-1},
    {"admitted":True,"candidate_generation":"2"},
    {"admitted":False,"candidate_generation":1},
    {"admitted":1},
])
def test_invalid_reply_rejected_before_send(kwargs):
    a,b=socket.socketpair()
    try:
        with pytest.raises(boundary.CandidateLocalTransportError,match="invalid_receipt"):
            boundary.send_holding_reply(a,**kwargs)
        b.settimeout(0.03)
        with pytest.raises(socket.timeout):
            b.recv(1)
    finally:
        a.close()
        b.close()


def test_broken_client_is_unconfirmed_not_fake_rejection():
    a,b=socket.socketpair()
    b.close()
    try:
        with pytest.raises(boundary.CandidateLocalTransportError,
                           match="reply_delivery_unconfirmed"):
            boundary.send_holding_reply(a,admitted=True,candidate_generation=1)
    finally:
        a.close()


def rootless_case(monkeypatch, *, scenario):
    if os.geteuid()==0 or os.getegid()==0:
        pytest.skip("non-root listener check runs in hosted Linux CI")
    tmp=tempfile.TemporaryDirectory(prefix="hermes-reply-test-")
    folder=Path(tmp.name)/"sock"
    folder.mkdir(mode=0o700)
    os.chmod(folder,0o2710)
    allowed=os.geteuid()+5000
    if scenario=="unauthorized":
        monkeypatch.setattr(listener,"_peer_credentials",
                            lambda s:(os.getpid(),os.geteuid(),os.getegid()))
    else:
        monkeypatch.setattr(listener,"_peer_credentials",
                            lambda s:(os.getpid(),allowed,os.getegid()))
    def fake_handler(conn,*,allowed_peer_uid,session_factory):
        assert allowed_peer_uid==allowed
        if scenario=="unauthorized":
            raise boundary.CandidateLocalTransportError("peer_not_authorized")
        if scenario=="reject":
            raise boundary.CandidateLocalTransportError("sensitive-task-name")
        return {"candidate_generation":7,"decision":"HOLD"}
    monkeypatch.setattr(listener,"serve_one_candidate_connection",fake_handler)
    if scenario=="reply_lost":
        def failure(*args,**kw):
            raise boundary.CandidateLocalTransportError("reply_delivery_unconfirmed")
        monkeypatch.setattr(listener,"send_holding_reply",failure)
    data={}
    def serve():
        try:
            data["result"]=listener.serve_rootless_candidate(
                socket_dir=folder,allowed_peer_uid=allowed,
                client_gid=os.getegid(),session_factory=lambda:None,
                max_connections=1,max_lifetime_seconds=2.0,
                emit_holding_replies=True,
            )
        except Exception as ex:
            data["exception"]=ex
    thread=threading.Thread(target=serve)
    thread.start()
    raw=b""
    try:
        deadline=time.monotonic()+1.4
        while not (folder/"broker.sock").exists() and time.monotonic()<deadline:
            time.sleep(0.01)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(1.5)
            client.connect(str(folder/"broker.sock"))
            client.sendall(b"{}\n")
            client.shutdown(socket.SHUT_WR)
            try:
                raw=client.recv(512)
            except ConnectionResetError:
                raw=b""
    finally:
        thread.join(timeout=3)
        tmp.cleanup()
    assert not thread.is_alive()
    assert "exception" not in data,data
    return raw,data["result"]


def test_unqualified_peer_receives_no_response(monkeypatch):
    raw,counts=rootless_case(monkeypatch,scenario="unauthorized")
    assert raw==b""
    assert counts["accepted_candidate_commands"]==0
    assert counts["rejected_connections"]==1
    assert counts["unconfirmed_socket_replies"]==0


def test_authorized_peer_receives_holding_receipt(monkeypatch):
    raw,counts=rootless_case(monkeypatch,scenario="accepted")
    value=decode(raw)
    assert value["accepted_source_transaction"] is True
    assert value["candidate_generation"]==7
    assert counts["accepted_candidate_commands"]==1
    assert counts["unconfirmed_socket_replies"]==0


def test_rejected_command_yields_redacted_denial(monkeypatch):
    raw,counts=rootless_case(monkeypatch,scenario="reject")
    value=decode(raw)
    assert value["accepted_source_transaction"] is False
    assert value["error"]=="request_denied"
    assert "sensitive-task" not in raw.decode()
    assert counts["rejected_connections"]==1


def test_reply_failure_never_reclassifies_committed_source(monkeypatch):
    raw,counts=rootless_case(monkeypatch,scenario="reply_lost")
    assert raw==b""
    assert counts["accepted_candidate_commands"]==1
    assert counts["rejected_connections"]==0
    assert counts["unconfirmed_socket_replies"]==1
    assert counts["socket_replies_durable"] is False


def test_listener_default_is_no_replies_for_backwards_compatibility():
    import inspect
    sig=inspect.signature(listener.serve_rootless_candidate)
    assert sig.parameters["emit_holding_replies"].default is False
