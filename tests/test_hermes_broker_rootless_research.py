"""Rootless research listener private socket safety, no live DB."""
from __future__ import annotations

import os
import socket
import stat
import tempfile
import threading
import time
from pathlib import Path

import pytest

from assistx import hermes_broker_rootless_research as listener

class NoDatabase:
    def __init__(self):
        self.calls=0
    def __call__(self):
        self.calls+=1
        raise AssertionError("research socket attempted database access")

def isolated_directory():
    tmp=tempfile.TemporaryDirectory(prefix="hermes-rootless-tests-")
    path=Path(tmp.name)/"private"
    path.mkdir(mode=0o700)
    os.chmod(path,0o2710)
    return tmp,path

@pytest.mark.parametrize("override",[
    {"allowed_peer_uid":os.geteuid()},
    {"allowed_peer_uid":0},{"allowed_peer_uid":True},
    {"client_gid":0},{"client_gid":True},
    {"max_connections":0},{"max_connections":9},
    {"max_lifetime_seconds":0},{"max_lifetime_seconds":31},
])
def test_invalid_config_refuses_to_bind(override):
    if os.geteuid()==0 or os.getegid()==0:
        pytest.skip("separate nonroot integration tested on hosted CI")
    tmp,path=isolated_directory()
    try:
        args={"socket_dir":path,"allowed_peer_uid":os.geteuid()+1234,
              "client_gid":os.getegid(),"max_connections":1,
              "max_lifetime_seconds":0.15,"session_factory":NoDatabase()}
        args.update(override)
        with pytest.raises(listener.CandidateBrokerListenerError):
            listener.serve_rootless_candidate(**args)
        assert not (path/"broker.sock").exists()
    finally:
        tmp.cleanup()

@pytest.mark.parametrize("mode",[0o700,0o710,0o770,0o777,0o2711,0o2770])
def test_wrong_private_dir_mode_refuses_without_chmod(mode):
    if os.geteuid()==0 or os.getegid()==0:
        pytest.skip("nonroot required")
    tmp,path=isolated_directory()
    try:
        os.chmod(path,mode)
        with pytest.raises(listener.CandidateBrokerListenerError,match="unsafe_socket_directory"):
            listener.serve_rootless_candidate(socket_dir=path,
                allowed_peer_uid=os.geteuid()+1234,client_gid=os.getegid(),
                session_factory=NoDatabase())
        assert stat.S_IMODE(path.stat().st_mode)==mode
        assert not (path/"broker.sock").exists()
    finally:
        tmp.cleanup()

def test_existing_socket_or_symlink_never_replaced():
    if os.geteuid()==0 or os.getegid()==0:
        pytest.skip("nonroot required")
    tmp,path=isolated_directory()
    try:
        target=path/"broker.sock"
        target.write_text("leave me")
        with pytest.raises(listener.CandidateBrokerListenerError,match="socket_path_exists"):
            listener.serve_rootless_candidate(socket_dir=path,
                allowed_peer_uid=os.geteuid()+1234,client_gid=os.getegid(),
                session_factory=NoDatabase())
        assert target.read_text()=="leave me"
        target.unlink()
        target.symlink_to("/etc/hosts")
        with pytest.raises(listener.CandidateBrokerListenerError,match="socket_path_exists"):
            listener.serve_rootless_candidate(socket_dir=path,
                allowed_peer_uid=os.geteuid()+1234,client_gid=os.getegid(),
                session_factory=NoDatabase())
        assert target.is_symlink()
    finally:
        tmp.cleanup()

def test_socket_bound_with_expected_mode_inode_and_cleanup(monkeypatch):
    if os.geteuid()==0 or os.getegid()==0:
        pytest.skip("nonroot required")
    tmp,path=isolated_directory()
    seen=[]
    def deny(conn,*,allowed_peer_uid,session_factory):
        seen.append(allowed_peer_uid)
        raise listener.CandidateLocalTransportError("synthetic-deny")
    monkeypatch.setattr(listener,"serve_one_candidate_connection",deny)
    data={}
    def worker():
        try:
            data["result"]=listener.serve_rootless_candidate(
                socket_dir=path,allowed_peer_uid=os.geteuid()+1234,
                client_gid=os.getegid(),session_factory=NoDatabase(),
                max_connections=1,max_lifetime_seconds=1.0)
        except Exception as e:
            data["error"]=e
    thread=threading.Thread(target=worker)
    thread.start()
    try:
        deadline=time.monotonic()+0.8
        while not (path/"broker.sock").exists() and time.monotonic()<deadline:
            time.sleep(0.005)
        sock=path/"broker.sock"
        assert sock.exists(),data
        mode=sock.stat()
        assert stat.S_ISSOCK(mode.st_mode)
        assert stat.S_IMODE(mode.st_mode)==0o660
        assert mode.st_uid==os.geteuid()
        assert mode.st_gid==os.getegid()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as c:
            c.connect(str(sock))
            c.sendall(b'{}\n')
    finally:
        thread.join(timeout=3)
        tmp.cleanup()
    assert not thread.is_alive()
    assert "error" not in data,data
    assert seen==[os.geteuid()+1234]
    assert data["result"]["accepted_candidate_commands"]==0
    assert data["result"]["rejected_connections"]==1
    assert data["result"]["decision"]=="HOLD"

def test_no_production_import_or_listener_invocation():
    root=Path(__file__).resolve().parents[1]/"src/assistx"
    for file in root.rglob("*.py"):
        if file.name.startswith("hermes_broker_") or file.name.startswith("hermes_source_"):
            continue
        assert "hermes_broker_rootless_research" not in file.read_text(),file
