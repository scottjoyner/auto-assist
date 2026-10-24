"""Offline deny-only contracts for exact-source Hermes normal Compose canary."""
from __future__ import annotations

import copy
from pathlib import Path

import hermes_split_root_normal_compose_canary as canary
import pytest

IMG = "sha256:" + "a" * 64
RO = Path("/home/scott/.cache/og-hermes-containment-12345678/knowledge")
RW = RO.parent / "worktree"


def _render():
    return {"services": {"hermes-split-synthetic": {
        "image": IMG, "pull_policy": "never",
        "entrypoint": ["/entrypoint.sh"],
        "command": ["python3", "-B", "-m", "assistx.strict_executor_main"],
        "restart": "no", "user": "1000:1000",
        "network_mode": "none", "read_only": True,
        "profiles": ["synthetic-canary"], "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "environment": {
            "HOME": "/workspace", "HERMES_HOME": "/workspace/hermes-home",
            "HERMES_CONFIG_HOME": "/workspace/.hermes",
            "PYTHONPATH": "/workspace:/app/src",
            "ASSISTX_OUTBOX_DB": "/workspace/outbox.sqlite",
            "ASSISTX_URL": "http://127.0.0.1:1",
            "ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN": "SYNTHETIC_BOOTSTRAP_ONLY",
            "ASSISTX_STRICT_EXECUTOR_AUTH": "true",
            "ASSISTX_REQUIRE_OWNER_BINDING": "true",
            "HERMES_REQUIRE_OWNER_BOUND_LAUNCH": "true",
            "ASSISTX_EXECUTOR_VERIFY_KEY_FILE": "/app/attestation/executor.pub",
            "HERMES_AGENT_ID": "hermes-synthetic-canary",
            "HERMES_SELFTASK_ENABLED": "false",
            "HERMES_SELFTASK_BULK_MODELS": "synthetic/model",
            "HERMES_KNOWLEDGE_READ_ROOT": "/research/knowledge",
            "HERMES_KNOWLEDGE_WRITE_ROOT": "/workspace/knowledge",
            "HERMES_REQUIRE_MOUNT_ISOLATION": "true",
            "HERMES_REQUIRE_SIGNED_CORPUS": "true",
            "HERMES_REQUIRE_CORPUS_CHECKPOINT": "true",
            "HERMES_CORPUS_CHECKPOINT_PATH": "/app/attestation/checkpoint.json",
            "HERMES_CORPUS_WITNESS_KEY_PATH": "/app/attestation/witness.pub",
            "HERMES_CORPUS_TRUST_KEY_PATH": "/app/attestation/trust.pub",
            "HERMES_KNOWLEDGE_WRITE_MOUNT": "/workspace",
            "HERMES_KNOWLEDGE_ROOT": "/workspace/knowledge",
            "HERMES_EVAL_PATH": "/workspace/knowledge/eval.json",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "volumes": [
            {"type": "bind", "source": str(RO),
             "target": "/research/knowledge", "read_only": True,
             "bind":{"create_host_path":False}},
            {"type": "bind", "source": str(RW),
             "target": "/workspace",
             "bind":{"create_host_path":False}},
        ],
    }}}


def test_valid_synthetic_compose_shape():
    assert canary.restricted_render(_render(), IMG, RO, RW)


@pytest.mark.parametrize("field,value", [
    ("network_mode", "host"), ("read_only", False),
    ("user", "0:0"), ("entrypoint", ["/bin/sh"]),
    ("image", "latest"), ("pull_policy", "always"),
    ("restart", "always"), ("profiles", []),
    ("cap_drop", []), ("security_opt", []),
])
def test_reject_unsafe_compose_changes(field, value):
    x = _render()
    x["services"]["hermes-split-synthetic"][field] = value
    assert not canary.restricted_render(x, IMG, RO, RW)


@pytest.mark.parametrize("field,value", [
    ("ports", ["18080:18080"]), ("networks", ["production"]),
    ("privileged", True), ("env_file", ["/etc/credentials"]),
    ("devices", ["/dev/kvm"]), ("build", {"context": "."}),
])
def test_extra_capabilities_are_denied(field, value):
    x = _render()
    x["services"]["hermes-split-synthetic"][field] = value
    assert not canary.restricted_render(x, IMG, RO, RW)


@pytest.mark.parametrize("mount",[0,1])
def test_missing_source_auto_creation_is_forbidden(mount):
    x = _render()
    volumes=x["services"]["hermes-split-synthetic"]["volumes"]
    volumes[mount]["bind"]["create_host_path"] = True
    assert not canary.restricted_render(x, IMG, RO, RW)
    del volumes[mount]["bind"]
    assert not canary.restricted_render(x, IMG, RO, RW)
    volumes[mount]["bind"] = {}
    assert not canary.restricted_render(x, IMG, RO, RW)


def test_no_unreviewed_bind_keys_or_aliases():
    for i in range(2):
        x=_render()
        x["services"]["hermes-split-synthetic"]["volumes"][i]["volume"]={}
        assert not canary.restricted_render(x,IMG,RO,RW)


def test_broad_rw_knowledge_denied():
    x = _render()
    x["services"]["hermes-split-synthetic"]["volumes"][0]["read_only"] = False
    assert not canary.restricted_render(x, IMG, RO, RW)


def test_hidden_ancestor_mount_denied():
    x = _render()
    x["services"]["hermes-split-synthetic"]["volumes"].append({
        "type": "bind", "source": "/media/scott/SSD_4TB",
        "target": "/media/scott/SSD_4TB"})
    assert not canary.restricted_render(x, IMG, RO, RW)


def test_source_root_and_write_root_cannot_be_swapped():
    x = _render()
    x["services"]["hermes-split-synthetic"]["environment"][
        "HERMES_KNOWLEDGE_WRITE_ROOT"] = "/research/knowledge"
    assert not canary.restricted_render(x, IMG, RO, RW)


def test_mutated_token_or_evaluation_path_denied():
    for env, value in (
        ("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "PRODUCTION_TOKEN"),
        ("HERMES_EVAL_PATH", "/research/knowledge/eval.json"),
        ("ASSISTX_URL", "http://production.example"),
    ):
        x = _render()
        x["services"]["hermes-split-synthetic"]["environment"][env] = value
        assert not canary.restricted_render(x, IMG, RO, RW)


def test_new_kernel_remote_mount_guard_is_pinned_in_disposable_source():
    from assistx import knowledge_roots
    source=Path(knowledge_roots.__file__)
    assert source.resolve()==(canary.ROOT/"src/assistx/knowledge_roots.py").resolve()
    import subprocess
    result=subprocess.run(["git","-C",str(canary.ROOT),"hash-object",
                           str(source)],capture_output=True,text=True,check=True)
    expected=result.stdout.strip()
    assert expected=="a7827f936f23a3e85103e26efa73e6c02d3a4124"
    assert expected in Path(canary.__file__).read_text()


def test_adapter_source_pin_includes_pre_mutation_mount_checks():
    import subprocess
    target=canary.ROOT/"src/assistx/agents/hermes_agent_adapter.py"
    git_blob=subprocess.run(["git","-C",str(canary.ROOT),"hash-object",
                            str(target)],capture_output=True,text=True,check=True).stdout.strip()
    assert git_blob=="7c4c03673526b660928aac2bbfebbf16b55c47c3"
    assert git_blob in Path(canary.__file__).read_text()
    source=target.read_text()
    eval_write=source.split("def save_eval(",1)[1].split("def ensure_model_env(",1)[0]
    assert eval_write.index("checked_write_path(KNOWLEDGE_WRITE_ROOT, EVAL_PATH)") < eval_write.index("os.makedirs(directory")
    selftask=source.split("def process_self_task(",1)[1].split("def process_task(",1)[0]
    check="checked_write_path(KNOWLEDGE_WRITE_ROOT, target_path)"
    assert selftask.index(check) < selftask.index("os.makedirs(os.path.dirname(target_path)")


def test_real_entrypoint_and_adapter_source_are_pinned():
    script = Path(canary.__file__).read_text()
    assert canary.sha(canary.ENTRY) == canary.EXPECTED_ENTRY_SHA
    assert '412affd187acffe2147f03088e1e126472f4667e' in script
    assert '"hash-object"' in script
    assert '"--network=none"' in script
    assert '"--pull=false"' in script
    assert '"--no-build"' in script
    assert '"--pull","never"' in script
    assert '"--project-name",project' in script
    assert '"--attach",cid' in script


def test_no_real_model_network_or_shell_in_sitecustomize():
    hook = canary.SITE.read_text()
    assert 'patch("requests.request"' in hook
    assert 'patch.object(subprocess, "Popen"' in hook
    assert 'ORIGINAL(once=True)' in hook
    assert 'runpy.run_path("/workspace/smoke.py"' in hook
    assert "SYNTHETIC_BOOTSTRAP_ONLY" in hook


def test_dry_run_reports_hold_and_cannot_call_docker(monkeypatch):
    import sys
    from subprocess import run
    result = run([sys.executable, str(canary.__file__), "--json"],
                 capture_output=True, text=True, check=True)
    assert '"decision": "HOLD"' in result.stdout
    assert '"physical_executed": false' in result.stdout


def test_every_required_check_and_blocker_declared():
    assert len(canary.CHECKS) == 16
    assert "kernel_ro_input_rw_scratch_admission_verified" in canary.CHECKS
    assert "signed_corpus_mutation_denied_at_relaunch" in canary.CHECKS
    assert "older_signed_manifest_replay_denied" in canary.CHECKS
    assert "signed_owner_positive_negative_and_heartbeat_denied_in_image" in canary.CHECKS
    assert "revocation_during_spawn_and_result_fenced_in_image" in canary.CHECKS
    assert "real_os_parent_and_descendant_process_group_fenced_in_image" in canary.CHECKS
    assert "http200_contradictory_task_owner_denied_in_image" in canary.CHECKS
    assert "all_disposable_docker_objects_removed" in canary.CHECKS
    assert "production_hermes_running_unchanged" in canary.CHECKS
    assert "independent_signed_image_and_source_attestation_missing" in canary.BLOCKERS


def test_real_process_group_probe_is_bounded_and_synthetic_only():
    from pathlib import Path
    src=Path(canary.SITE).read_text()
    assert "exercise_real_process_group_fence()" in src
    assert '"HERMES_SYNTHETIC_REAL_PROCESS_GROUP_FENCED_PASS"' in src
    assert '"-S","-u","-c",program,str(marker)' in src
    assert '"PYTHONPATH"]=""' in src
    assert '"OPENBLAS_NUM_THREADS"]="1"' in src
    assert '"HERMES_EXECUTOR_TOKEN","OPENAI_API_KEY"' in src
    assert '"SYNTHETIC_PROCESS_GROUP_ONLY"' in src
    assert '"start_new_session") is True' in src
    assert "signal.signal(signal.SIGTERM,signal.SIG_IGN)" in src
    assert "synthetic-child-installed-sigignore" in src
    assert "synthetic child did not arm SIGTERM ignore" in src
    assert 'marker.unlink(missing_ok=True)' in src
    assert 'time.monotonic()+8' in src
    assert 'os.killpg(parent,signal.SIGKILL)' in src


def test_real_process_group_probe_does_not_read_real_corpus_or_use_network():
    from pathlib import Path
    src=Path(canary.SITE).read_text()
    part=src.split('def exercise_real_process_group_fence():',1)[1].split(
        'def synthetic_dispatch():',1)[0]
    assert '/workspace/synthetic-process-group-termination.json' in part
    assert 'only_signed_heartbeat' not in part
    assert 'heartbeat_ok' in part
    assert 'REAL_POPEN' in part
    for forbidden in ('/media/scott/SSD_4TB','/media/scott/NAS5',
                      'ssh ', 'curl ', 'wget ', 'docker run','docker exec'):
        assert forbidden not in part
