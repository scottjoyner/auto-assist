"""Offline canary unit tests; cannot create real containers."""
from pathlib import Path
import importlib.util
import json
import tempfile
from unittest.mock import patch
from types import SimpleNamespace

SPEC=importlib.util.spec_from_file_location(
    "k2_network_none",
    Path(__file__).resolve().parents[1]/"scripts/k2-no-network-cpu-repro.py")
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_docker_args_strict_network_none():
    args=module.docker_args("k2-cpu-repro-aabbcc")
    assert args[args.index("--network")+1]=="none"
    assert "--publish" not in args and "-p" not in args
    assert "--cap-drop" in args and "ALL" in args
    assert "--read-only" in args
    assert "--pull" in args and "never" in args
    assert "--user" in args and "1000:1000" in args
    assert args[args.index("--host")+1]=="127.0.0.1"
    assert args[args.index("--n-gpu-layers")+1]=="0"
    assert "--rm" in args
    mounts=[args[i+1] for i,x in enumerate(args) if x=="--mount"]
    assert len(mounts)==3
    assert all("readonly" in x for x in mounts)
    assert "python:3.12-slim-bookworm" in args


def test_guard_block_never_launches():
    with patch.object(module,"guard",return_value=["model_sha256_mismatch"]):
        with patch.object(module,"run") as proc:
            result=module.run_experiment()
    assert result["status"]=="HOLD"
    assert result["cleanup"] is False
    proc.assert_not_called()


def test_mocked_isolated_pass_and_container_cleanup():
    results={"sequential":[
        {"http":200,"usage_valid":True,"prompt_tokens":12,"completion_tokens":8},
        {"http":200,"usage_valid":True,"prompt_tokens":13,"completion_tokens":9}],
        "concurrent":[
        {"http":200,"usage_valid":True,"prompt_tokens":14,"completion_tokens":10},
        {"http":200,"usage_valid":True,"prompt_tokens":15,"completion_tokens":11}]}
    def executor(argv,timeout=20):
        if argv[1]=="run":
            return SimpleNamespace(returncode=0,stdout="container-1",stderr="")
        if argv[1]=="inspect" and "--format" in argv:
            if "{{json .HostConfig}}" in argv:
                return SimpleNamespace(returncode=0,stdout=json.dumps({
                    "NetworkMode":"none","PortBindings":{},"ReadonlyRootfs":True,
                    "Privileged":False,"CapDrop":["ALL"]}),stderr="")
            return SimpleNamespace(returncode=0,stdout="true",stderr="")
        if argv[1]=="exec":
            if "/health" in argv[-1]:
                return SimpleNamespace(returncode=0,stdout="200\n",stderr="")
            return SimpleNamespace(returncode=0,stdout=json.dumps(results),stderr="")
        if argv[1]=="rm":
            assert argv[2]=="-f"
            assert argv[3].startswith("k2-cpu-repro-")
            return SimpleNamespace(returncode=0,stdout="",stderr="")
        raise AssertionError(argv)
    with patch.object(module,"guard",return_value=[]),patch.object(module,"run",side_effect=executor):
        result=module.run_experiment()
    assert result["status"]=="CROSS_RUNTIME_SYNTHETIC_PASS"
    assert result["cleanup"]
    assert result["network_none_verified"]
    assert result["measured_prompt_tokens"]==54
    assert result["measured_output_tokens"]==38
    assert not result["can_dispatch"]


def test_missing_network_isolation_denies_client_and_removes_container():
    calls=[]
    def executor(argv,timeout=20):
        calls.append(argv)
        if argv[1]=="run":
            return SimpleNamespace(returncode=0,stdout="c",stderr="")
        if argv[1]=="inspect":
            return SimpleNamespace(returncode=0,stdout=json.dumps({
                "NetworkMode":"bridge","PortBindings":{},"ReadonlyRootfs":True,
                "Privileged":False,"CapDrop":["ALL"]}),stderr="")
        if argv[1]=="rm":
            return SimpleNamespace(returncode=0,stdout="",stderr="")
        raise AssertionError(argv)
    with patch.object(module,"guard",return_value=[]),patch.object(module,"run",side_effect=executor):
        result=module.run_experiment()
    assert result["status"]=="HOLD"
    assert result["cleanup"]
    assert result["reason"]=="unexpected_container_network_permissions"
    assert not any(x[1]=="exec" for x in calls)


def test_cleanup_failure_is_nonpassing_even_after_success():
    def executor(argv,timeout=20):
        if argv[1]=="run":
            return SimpleNamespace(returncode=0,stdout="",stderr="")
        if argv[1]=="inspect":
            return SimpleNamespace(returncode=0,stdout=json.dumps({
                "NetworkMode":"none","PortBindings":{},"ReadonlyRootfs":True,
                "Privileged":False,"CapDrop":["ALL"]}),stderr="")
        if argv[1]=="exec":
            if "/health" in argv[-1]:
                return SimpleNamespace(returncode=0,stdout="200",stderr="")
            rows={"sequential":[{"usage_valid":True,"http":200,"prompt_tokens":1,
                      "completion_tokens":1}]*2,
                  "concurrent":[{"usage_valid":True,"http":200,"prompt_tokens":1,
                      "completion_tokens":1}]*2}
            return SimpleNamespace(returncode=0,stdout=json.dumps(rows),stderr="")
        if argv[1]=="rm":
            return SimpleNamespace(returncode=1,stdout="",stderr="unauthorized")
        raise AssertionError
    with patch.object(module,"guard",return_value=[]),patch.object(module,"run",side_effect=executor):
        result=module.run_experiment()
    assert result["status"]=="HOLD_CLEANUP_UNVERIFIED"
    assert not result["cleanup"]
