"""Test-only hook for real normal Hermes startup + patched adapter.

No real tasks or model/network subprocesses are authorized.
"""
from __future__ import annotations
import os
import pathlib
import runpy
import subprocess
import sys
import time
import threading
import signal
from unittest.mock import patch
from assistx.agents import hermes_agent_adapter as adapter

REAL_POPEN = subprocess.Popen
ORIGINAL = adapter.run_loop
COUNT = [0]

class EmptyReply:
    def raise_for_status(self):
        pass
    def json(self):
        return {"items": []}

class TaskHeartbeatReply:
    def __init__(self, fixture, *, session_id):
        self.fixture=fixture
        self.session_id=session_id
    def raise_for_status(self):
        pass
    def json(self):
        claims=self.fixture["claims"]
        return {"task":{
            "id":claims["task_id"],
            "claim_id":claims["claim_id"],
            "claimed_by":claims["agent_id"],
            "agent_session_id":self.session_id,
            "owner_issuer":claims["owner_issuer"],
            "owner_subject":claims["owner_subject"],
            "status":"RUNNING",
            "lease_expires_at_ts":int(__import__("time").time()*1000)+1_800_000,
        }}

def fake_http(method, url, **kwargs):
    assert method == "GET" and url.startswith("http://127.0.0.1:1/api/agent/tasks?")
    assert kwargs.get("headers", {}).get("Authorization") == "Bearer SYNTHETIC_BOOTSTRAP_ONLY"
    assert kwargs.get("headers", {}).get("X-AssistX-Agent-Id") == "hermes-synthetic-canary"
    COUNT[0] += 1
    return EmptyReply()

def forbidden_child(*args, **kwargs):
    raise AssertionError("Hermes subprocess forbidden in synthetic mode")

def exercise_signed_owner_launch():
    # Actual patched strict adapter and image-baked Ed25519 public key.
    # Network and model subprocesses remain completely stubbed.
    import json
    fixture=json.loads(pathlib.Path("/workspace/owner_launch_fixture.json").read_text())
    claim=fixture["claims"]
    client=adapter.AssistXClient()
    client.task_token=fixture["token"]
    client.task_claims={k:v for k,v in claim.items() if k!="jti"}
    client._active_task_id=claim["task_id"]
    client._active_claim_id=claim["claim_id"]
    client._active_session_id="synthetic-session"
    events=[]
    def only_signed_heartbeat(method,url,**kwargs):
        assert method=="POST" and url.endswith("/api/tasks/synthetic-task/heartbeat")
        assert kwargs["headers"]["Authorization"]=="Bearer "+fixture["token"]
        assert kwargs["json"]["claim_id"]=="synthetic-claim"
        events.append("fresh-lease")
        return TaskHeartbeatReply(fixture,session_id="synthetic-session")
    class SyntheticModelExit:
        returncode=0
        pid=9999999
        def __init__(self, cmd, **kwargs):
            assert cmd[0]==adapter.HERMES_BIN
            assert kwargs["env"]["HERMES_EXECUTOR_TOKEN"]==fixture["token"]
            events.append("fake-model-only")
        def poll(self): return 0
        def communicate(self): return ("Session ID: synthetic-research\nSAFE", "")
    def no_process(*args,**kwargs):
        return SyntheticModelExit(*args,**kwargs)
    with patch("requests.request",side_effect=only_signed_heartbeat), patch.object(
            subprocess,"Popen",side_effect=no_process):
        accepted=adapter.run_hermes(
            "SYNTHETIC_NO_PROVIDER",model="synthetic/model")
        assert accepted["success"] is True, accepted.get("error")
        assert events==["fresh-lease","fake-model-only"]
        client.task_claims["owner_subject"]="forged-research-owner"
        denied=adapter.run_hermes(
            "SYNTHETIC_MUST_DENY",model="synthetic/model")
        assert not denied["success"] and denied["error"]=="owner_bound_launch_denied"
        assert events==["fresh-lease","fake-model-only"]
        assert client._lease_lost.is_set()
    # Separate fresh signed claim; a failed *fresh* heartbeat must deny
    # without calling even the synthetic fake process constructor.
    fresh=adapter.AssistXClient()
    fresh.task_token=fixture["token"]
    fresh.task_claims={k:v for k,v in claim.items() if k!="jti"}
    fresh._active_task_id=claim["task_id"]
    fresh._active_claim_id=claim["claim_id"]
    fresh._active_session_id="synthetic-session"
    from requests.exceptions import ConnectionError as NetworkDenied
    with patch("requests.request",side_effect=NetworkDenied("synthetic offline")), patch.object(
            subprocess,"Popen",side_effect=forbidden_child):
        failed=adapter.run_hermes(
            "SYNTHETIC_OFFLINE_MUST_DENY",model="synthetic/model")
    assert failed["error"]=="owner_bound_launch_denied"
    assert fresh._lease_lost.is_set()
    # Even HTTP 200 must not authorize a Task record for another owner.
    echoed=adapter.AssistXClient()
    echoed.task_token=fixture["token"]
    echoed.task_claims={k:v for k,v in claim.items() if k!="jti"}
    echoed._active_task_id=claim["task_id"]
    echoed._active_claim_id=claim["claim_id"]
    echoed._active_session_id="synthetic-session"
    def contradictory_200(method,url,**kwargs):
        assert method=="POST" and url.endswith("/heartbeat")
        reply=TaskHeartbeatReply(fixture,session_id="synthetic-session").json()
        reply["task"]["owner_subject"]="forged-revoked-owner"
        class Contradiction:
            def raise_for_status(self):
                pass
            def json(self):
                return reply
        return Contradiction()
    with patch("requests.request",side_effect=contradictory_200), patch.object(
            subprocess,"Popen",side_effect=forbidden_child):
        denied_200=adapter.run_hermes(
            "SYNTHETIC_HTTP200_WRONG_OWNER_MUST_DENY",model="synthetic/model")
    assert denied_200["error"]=="owner_bound_launch_denied"
    assert echoed._lease_lost.is_set()
    print("HERMES_SYNTHETIC_HTTP200_OWNER_MISMATCH_DENIED_PASS",flush=True)
    print("HERMES_SYNTHETIC_SIGNED_OWNER_LAUNCH_PASS",flush=True)
    # Test the handoff race and completion race in the REAL patched
    # executor adapter. Neither test creates a model subprocess.
    def ready_client():
        runner=adapter.AssistXClient()
        runner.task_token=fixture["token"]
        runner.task_claims={k:v for k,v in claim.items() if k!="jti"}
        runner._active_task_id=claim["task_id"]
        runner._active_claim_id=claim["claim_id"]
        runner._active_session_id="synthetic-session"
        return runner
    handoff=ready_client()
    class InstantExit:
        pid=9999999
        returncode=0
        def poll(self): return 0
        def communicate(self): return ("Session ID: FORBIDDEN\\nSECRET", "")
    def revoke_during_popen(*args,**kw):
        handoff._lease_lost.set()
        return InstantExit()
    with patch("requests.request",side_effect=only_signed_heartbeat), patch.object(
            subprocess,"Popen",side_effect=revoke_during_popen):
        result=adapter.run_hermes("SYNTHETIC_REVOKE_AT_POPEN",
                                  model="synthetic/model")
    assert result["error"]=="executor_lease_lost"
    assert result["output"]=="" and result["session_id"] is None
    assert handoff._active_process is None

    completing=ready_client()
    class RevokeOnCommunicate(InstantExit):
        def communicate(self):
            completing._lease_lost.set()
            return ("Session ID: FORBIDDEN\\nSECRET", "")
    with patch("requests.request",side_effect=only_signed_heartbeat), patch.object(
            subprocess,"Popen",side_effect=lambda *a,**kw:RevokeOnCommunicate()):
        result=adapter.run_hermes("SYNTHETIC_REVOKE_DURING_OUTPUT",
                                  model="synthetic/model")
    assert result["error"]=="executor_lease_lost"
    assert result["output"]=="" and result["session_id"] is None
    assert completing._active_process is None
    print("HERMES_SYNTHETIC_REVOKED_OUTPUT_FENCED_PASS",flush=True)

def exercise_real_process_group_fence():
    """Actual OS SIGTERM fence of two harmless Python processes inside the
    disposable networkless container. No Hermes binary, task or model is run.
    """
    import json
    marker=pathlib.Path("/workspace/synthetic-process-group-termination.json")
    ready_marker=pathlib.Path("/workspace/synthetic-child-installed-sigignore")
    marker.unlink(missing_ok=True)
    ready_marker.unlink(missing_ok=True)
    fixture=json.loads(pathlib.Path("/workspace/owner_launch_fixture.json").read_text())
    claims=fixture["claims"]
    client=adapter.AssistXClient()
    client.task_token=fixture["token"]
    client.task_claims={k:v for k,v in claims.items() if k!="jti"}
    client._active_task_id=claims["task_id"]
    client._active_claim_id=claims["claim_id"]
    client._active_session_id="synthetic-physical-process-group"
    child_pid=[]
    started=threading.Event()
    exited=threading.Event()
    program = """
import json,os,subprocess,sys,time,pathlib
child_script = ("import signal,time,pathlib,sys;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "pathlib.Path(sys.argv[1]).write_text('READY');"
                "time.sleep(30)")
kid=subprocess.Popen([sys.executable,'-S','-c',child_script,sys.argv[2]],
                     stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
deadline=time.monotonic()+5
while time.monotonic()<deadline and not pathlib.Path(sys.argv[2]).is_file():
    time.sleep(0.02)
if not pathlib.Path(sys.argv[2]).is_file():
    raise RuntimeError('synthetic child did not arm SIGTERM ignore')
pathlib.Path(sys.argv[1]).write_text(
    json.dumps({'parent':os.getpid(),'child':kid.pid}))
print('SYNTHETIC_SECRET_OUTPUT_MUST_NEVER_RETURN',flush=True)
time.sleep(30)
"""
    def synthetic_popen(cmd,**kwargs):
        assert cmd[0]==adapter.HERMES_BIN and "SYNTHETIC_PROCESS_GROUP_ONLY" in cmd
        assert kwargs.get("start_new_session") is True
        # Keep test-only signed JWT out of even the synthetic Python children.
        env=dict(kwargs["env"])
        for key in ("HERMES_EXECUTOR_TOKEN","OPENAI_API_KEY"):
            env.pop(key,None)
        env["PYTHONPATH"]=""
        env["OPENBLAS_NUM_THREADS"]="1"
        env["OMP_NUM_THREADS"]="1"
        # -S disables sitecustomize/autoload for both harmless test children.
        return REAL_POPEN([sys.executable,"-S","-u","-c",program,str(marker),str(ready_marker)],
                          **{**kwargs,"env":env})
    def revoke_after_both_processes_exist():
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if marker.exists():
                try:
                    witness=json.loads(marker.read_text())
                    assert type(witness["parent"]) is int
                    assert type(witness["child"]) is int
                    child_pid.extend([witness["parent"],witness["child"]])
                    started.set()
                    client._lease_lost.set()
                    exited.set()
                    return
                except (ValueError,KeyError):
                    pass
            time.sleep(0.02)
        exited.set()
    watcher=threading.Thread(target=revoke_after_both_processes_exist,daemon=True)
    watcher.start()
    def heartbeat_ok(method,url,**kwargs):
        assert method=="POST" and url.endswith("/api/tasks/synthetic-task/heartbeat")
        return TaskHeartbeatReply(
            fixture,session_id="synthetic-physical-process-group")
    try:
        with patch("requests.request",side_effect=heartbeat_ok), patch.object(
                subprocess,"Popen",side_effect=synthetic_popen):
            result=adapter.run_hermes(
                "SYNTHETIC_PROCESS_GROUP_ONLY",model="synthetic/model",timeout=10)
        watcher.join(timeout=9)
        assert not watcher.is_alive() and exited.is_set() and started.is_set()
        assert len(child_pid)==2 and child_pid[0]!=child_pid[1]
        assert result["error"]=="executor_lease_lost"
        assert result["output"]=="" and result["session_id"] is None
        assert client._active_process is None
        def non_zombie(pid):
            stat=pathlib.Path(f"/proc/{pid}/stat")
            if not stat.exists():return False
            return stat.read_text().split(") ",1)[1][0] != "Z"
        until=time.monotonic()+3
        while time.monotonic()<until and any(non_zombie(pid) for pid in child_pid):
            time.sleep(0.03)
        assert not any(non_zombie(pid) for pid in child_pid), "descendant survived group termination"
    finally:
        watcher.join(timeout=1)
        # Bound a synthetic test failure: never leave harmless test processes alive.
        if child_pid:
            parent=child_pid[0]
            try:
                if os.getpgid(parent)==parent:
                    os.killpg(parent,signal.SIGKILL)
            except ProcessLookupError:
                pass
        marker.unlink(missing_ok=True)
        ready_marker.unlink(missing_ok=True)
    print("HERMES_SYNTHETIC_REAL_PROCESS_GROUP_FENCED_PASS",flush=True)

def synthetic_dispatch():
    assert os.geteuid() == 1000 and adapter._strict_executor_auth_installed
    assert adapter.KNOWLEDGE_SPLIT_ENABLED
    assert os.environ["HERMES_REQUIRE_MOUNT_ISOLATION"] == "true"
    assert os.environ["HERMES_REQUIRE_OWNER_BOUND_LAUNCH"] == "true"
    assert os.environ["ASSISTX_REQUIRE_OWNER_BINDING"] == "true"
    assert os.environ["ASSISTX_EXECUTOR_VERIFY_KEY_FILE"] == "/app/attestation/executor.pub"
    assert pathlib.Path("/app/attestation/executor.pub").is_file()
    assert os.environ["HERMES_REQUIRE_SIGNED_CORPUS"] == "true"
    assert os.environ["HERMES_REQUIRE_CORPUS_CHECKPOINT"] == "true"
    assert os.environ["HERMES_CORPUS_CHECKPOINT_PATH"] == "/app/attestation/checkpoint.json"
    assert os.environ["HERMES_CORPUS_WITNESS_KEY_PATH"] == "/app/attestation/witness.pub"
    assert os.environ["HERMES_CORPUS_TRUST_KEY_PATH"] == "/app/attestation/trust.pub"
    assert os.environ["HERMES_KNOWLEDGE_WRITE_MOUNT"] == "/workspace"
    assert pathlib.Path("/workspace/hermes-home/config.yaml").is_file()
    assert pathlib.Path("/workspace/.hermes/config.yaml").is_symlink()
    with patch("requests.request", side_effect=fake_http),          patch.object(subprocess, "Popen", side_effect=forbidden_child),          patch.object(adapter, "fleet", None),          patch.object(adapter, "swarm_memory", None),          patch.object(adapter, "SELFTASK_BULK_MODELS", ["synthetic/model"]),          patch.object(adapter, "HERMES_DELEGATE_OPENCODE_TIERS", []):
        runpy.run_path("/workspace/smoke.py", run_name="__main__")
        exercise_signed_owner_launch()
        exercise_real_process_group_fence()
        # The real adapter's background job launcher is intentionally
        # disabled only for the no-task poll after the self-task fixture.
        with patch.object(adapter, "SELFTASK_BULK_MODELS", []):
            ORIGINAL(once=True)
    assert COUNT[0] == 1
    print("HERMES_SPLIT_NORMAL_ENTRYPOINT_PASS", flush=True)

adapter.run_loop = synthetic_dispatch
