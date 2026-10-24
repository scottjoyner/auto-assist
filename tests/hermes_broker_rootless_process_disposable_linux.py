#!/usr/bin/env python3
"""Ephemeral hosted rootless broker process and distinct-UID socket fixture.

Never use with fleet services. It requires GitHub Actions + explicit synthetic
flag and tests only a no-database stub. No database usernames/passwords,
network APIs, NAS paths, systemd units, privileged Task mutations or
production socket directories are created.
"""
from __future__ import annotations

if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("dedicated ephemeral rootless Linux witness only")

import importlib.util
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import sys
import tempfile
import time
import types

assert os.environ.get("HERMES_ROOTLESS_CI")=="synthetic-only"
assert os.environ.get("GITHUB_ACTIONS")=="true"
assert "NEO4J_URI" not in os.environ
assert "NEO4J_PASSWORD" not in os.environ

src=Path(__file__).resolve().parents[1]/"src/assistx"
pkg=types.ModuleType("assistx")
pkg.__path__=[str(src)]
sys.modules["assistx"]=pkg
for name in (
    "hermes_claim_census","hermes_source_generation_research",
    "hermes_source_heartbeat_research","hermes_source_completion_research",
    "hermes_broker_command_research","hermes_broker_unix_boundary_research",
    "hermes_broker_rootless_research",
):
    spec=importlib.util.spec_from_file_location("assistx."+name,src/(name+".py"))
    module=importlib.util.module_from_spec(spec)
    sys.modules["assistx."+name]=module
    spec.loader.exec_module(module)
from assistx import hermes_broker_rootless_research as broker
from assistx import hermes_broker_unix_boundary_research as boundary

def envelope():
    return json.dumps({
        "version":1,
        "command":{"operation":"complete","expected_generation":2,
                   "task_id":"synthetic-task","claim_id":"synthetic-claim",
                   "session_id":"synthetic-session","attempt":1}
    },separators=(",",":")).encode()+b"\n"

def child_main(socket_dir: str,allowed_peer_uid: int,client_gid: int) -> None:
    assert os.geteuid()!=0 and os.getegid()!=0
    assert os.geteuid()!=allowed_peer_uid
    def stub(command,*,trusted_channel_principal,session_factory):
        assert command["operation"]=="complete"
        assert trusted_channel_principal=="hermes-local"
        return {"candidate_generation":3}
    boundary.dispatch_candidate_command=stub
    def forbidden_db_session():
        raise AssertionError("synthetic rootless broker opened a DB session")
    outcome=broker.serve_rootless_candidate(
        socket_dir=Path(socket_dir),
        allowed_peer_uid=allowed_peer_uid,
        client_gid=client_gid,
        session_factory=forbidden_db_session,
        max_connections=2,
        max_lifetime_seconds=8,
    )
    assert outcome["accepted_candidate_commands"]==1,outcome
    assert outcome["rejected_connections"]==1,outcome
    assert outcome["production_dispatch_authorized"] is False
    assert outcome["broker_exclusive_credentials_verified"] is False
    assert outcome["decision"]=="HOLD"
    print("PASS actual_nonroot_listener_one_reject_one_accepted",flush=True)
    print("PRODUCTION_SOCKET_TOUCHED=0;PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD",flush=True)

def drop_to(uid: int,gid: int):
    def _drop():
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    return _drop

def run_client(sock: Path,data: bytes,uid: int,gid: int):
    code=("import socket,sys;"
          "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
          "s.connect(sys.argv[1]);"
          "s.sendall(bytes.fromhex(sys.argv[2]));"
          "s.close()")
    proc=subprocess.run(
        [sys.executable,"-c",code,str(sock),data.hex()],
        preexec_fn=drop_to(uid,gid),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=8,
        check=False,
    )
    assert proc.returncode==0, "ephemeral_client_failed"

def main():
    assert os.geteuid()==0,"CI setup requires only ephemeral test-root"
    client=pwd.getpwnam("nobody")
    server=pwd.getpwnam("daemon")
    assert server.pw_uid>0 and client.pw_uid>0
    assert server.pw_uid!=client.pw_uid
    assert server.pw_gid>0 and client.pw_gid>0
    with tempfile.TemporaryDirectory(prefix="hermes-rootless-ci-") as temp:
        basedir=Path(temp)
        os.chmod(basedir,0o755)
        private=basedir/"private"
        private.mkdir(mode=0o700)
        os.chown(private,server.pw_uid,client.pw_gid)
        os.chmod(private,0o2710)
        assert stat.S_IMODE(private.stat().st_mode)==0o2710
        sock=private/"broker.sock"
        args=[
            sys.executable,str(Path(__file__).resolve()),"--child",
            str(private),str(client.pw_uid),str(client.pw_gid),
        ]
        p=subprocess.Popen(
            args,preexec_fn=drop_to(server.pw_uid,server.pw_gid),
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        )
        try:
            deadline=time.monotonic()+7
            while not sock.exists() and p.poll() is None and time.monotonic()<deadline:
                time.sleep(0.02)
            assert p.poll() is None and sock.exists(),"isolated_rootless_server_failed_to_bind"
            status=Path(f"/proc/{p.pid}/status").read_text()
            uid_line=next(x for x in status.splitlines() if x.startswith("Uid:"))
            assert int(uid_line.split()[1])==server.pw_uid
            meta=sock.stat()
            assert meta.st_uid==server.pw_uid
            assert meta.st_gid==client.pw_gid
            assert stat.S_IMODE(meta.st_mode)==0o660
            print("PASS actual_process_rootless_private_socket_permissions",flush=True)
            run_client(sock,envelope(),server.pw_uid,server.pw_gid)
            run_client(sock,envelope(),client.pw_uid,client.pw_gid)
            stdout,stderr=p.communicate(timeout=9)
            assert p.returncode==0,"isolated_rootless_server_exited_unsafely"
            assert b"PASS actual_nonroot_listener_one_reject_one_accepted" in stdout
            assert b"PRODUCTION_SOCKET_TOUCHED=0" in stdout
            assert not sock.exists(),"server_socket_not_cleaned"
            print("PASS separate_UID_client_admitted_same_UID_rejected",flush=True)
        finally:
            if p.poll() is None:
                p.kill()
                p.communicate(timeout=5)
    print("HERMES_ROOTLESS_CANDIDATE_PROCESS_CI_PASS=2",flush=True)
    print("PRODUCTION_SOCKET_TOUCHED=0;PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD",flush=True)

if len(sys.argv)==5 and sys.argv[1]=="--child":
    child_main(sys.argv[2],int(sys.argv[3]),int(sys.argv[4]))
elif len(sys.argv)==1:
    main()
else:
    raise SystemExit("invalid synthetic runner invocation")
