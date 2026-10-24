#!/usr/bin/env python3
"""Hosted-CI-only physical Linux AF_UNIX / SO_PEERCRED identity witness.

Requires an ephemeral GitHub runner, root via sudo, and uses synthetic
unprivileged nobody only. No Tailscale, Neo4j credentials, database access,
production socket, auth keys, or live processes are touched.
"""
from __future__ import annotations

if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("explicit isolated root CI peer credential probe only")

import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys
import tempfile

assert os.environ.get("HERMES_PEER_ISOLATION_CI")=="synthetic-only"
assert os.environ.get("GITHUB_ACTIONS")=="true"
assert os.geteuid()==0

from assistx import hermes_broker_unix_boundary_research as boundary

nobody=pwd.getpwnam("nobody")
assert nobody.pw_uid>0 and nobody.pw_uid!=os.geteuid()
assert nobody.pw_gid>=0

PAYLOAD=json.dumps({
    "version":1,
    "command":{
        "operation":"complete",
        "expected_generation":2,
        "task_id":"synthetic-task",
        "claim_id":"synthetic-claim",
        "session_id":"synthetic-session",
        "attempt":1,
    }
},separators=(",",":")).encode()+b"\n"

# Inject a research adapter receipt, not a DB session or live side effect.
seen=[]
def stub_dispatch(command,*,trusted_channel_principal,session_factory):
    seen.append((command,trusted_channel_principal))
    assert command["operation"]=="complete"
    assert trusted_channel_principal=="hermes-local"
    return {"candidate_generation":3}

boundary.dispatch_candidate_command=stub_dispatch

def child_code():
    return (
        "import socket,sys;"
        "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
        "s.connect(sys.argv[1]);"
        "s.sendall(bytes.fromhex(sys.argv[2]));"
        "s.close()"
    )

def spawn_client(path,raw,*,uid=None,gid=None):
    def drop():
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    return subprocess.Popen(
        [sys.executable,"-c",child_code(),str(path),raw.hex()],
        preexec_fn=drop if uid is not None else None,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

def no_db_session():
    raise AssertionError("peer isolation canary opened DB connection")

with tempfile.TemporaryDirectory(prefix="hermes-peer-isolation-ci-") as dirname:
    os.chmod(dirname,0o755)  # disposable localhost directory only
    path=Path(dirname)/"research.sock"
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        os.chmod(path,0o666)  # deliberately allow connect; kernel UID must deny
        listener.listen(2)
        listener.settimeout(8)

        # Unauthorized broker-UID peer must be denied despite socket reachability.
        client=spawn_client(path,PAYLOAD)
        try:
            conn,_=listener.accept()
            with conn:
                pid,uid,gid=boundary._peer_credentials(conn)
                assert uid==os.geteuid()
                try:
                    boundary.serve_one_candidate_connection(
                        conn,allowed_peer_uid=nobody.pw_uid,
                        session_factory=no_db_session)
                except boundary.CandidateLocalTransportError as e:
                    assert str(e)=="peer_not_authorized"
                else:
                    raise AssertionError("same UID connection accepted")
            assert client.wait(timeout=8)==0
        finally:
            if client.poll() is None:
                client.kill()
        print("PASS actual_kernel_same_UID_connection_denied",flush=True)

        # Actual different-process, different-UID client is admitted by
        # SO_PEERCRED before forwarding ONLY the fixed command.
        client=spawn_client(path,PAYLOAD,uid=nobody.pw_uid,gid=nobody.pw_gid)
        try:
            conn,_=listener.accept()
            with conn:
                pid,uid,gid=boundary._peer_credentials(conn)
                assert uid==nobody.pw_uid,(uid,nobody.pw_uid)
                assert gid==nobody.pw_gid
                result=boundary.serve_one_candidate_connection(
                    conn,allowed_peer_uid=nobody.pw_uid,
                    session_factory=no_db_session)
            assert client.wait(timeout=8)==0
        finally:
            if client.poll() is None:
                client.kill()
        assert len(seen)==1
        assert result["transport_kernel_peer_uid_matched"] is True
        assert result["candidate_generation"]==3
        assert result["transport_fleet_authenticated"] is False
        assert result["broker_exclusive_credentials_verified"] is False
        assert result["all_writers_generation_fenced"] is False
        assert result["production_dispatch_authorized"] is False
        assert result["decision"]=="HOLD"
        print("PASS actual_kernel_distinct_UID_peer_fixed_command_HOLD",flush=True)

print("HERMES_UNIX_PEER_ISOLATION_CI_PASS=2",flush=True)
print("PRODUCTION_SOCKETS_TOUCHED=0;PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD",flush=True)
