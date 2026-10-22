"""Offline deny-only contracts for exact-source Hermes normal Compose canary."""
from __future__ import annotations
import copy
from pathlib import Path
import pytest
import hermes_split_root_normal_compose_canary as canary

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
             "target": "/research/knowledge", "read_only": True},
            {"type": "bind", "source": str(RW),
             "target": "/workspace"},
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
    from subprocess import run
    import sys
    result = run([sys.executable, str(canary.__file__), "--json"],
                 capture_output=True, text=True, check=True)
    assert '"decision": "HOLD"' in result.stdout
    assert '"physical_executed": false' in result.stdout


def test_every_required_check_and_blocker_declared():
    assert len(canary.CHECKS) == 13
    assert "kernel_ro_input_rw_scratch_admission_verified" in canary.CHECKS
    assert "signed_corpus_mutation_denied_at_relaunch" in canary.CHECKS
    assert "older_signed_manifest_replay_denied" in canary.CHECKS
    assert "signed_owner_positive_negative_and_heartbeat_denied_in_image" in canary.CHECKS
    assert "all_disposable_docker_objects_removed" in canary.CHECKS
    assert "production_hermes_running_unchanged" in canary.CHECKS
    assert "independent_signed_image_and_source_attestation_missing" in canary.BLOCKERS
