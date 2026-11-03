"""Synthetic security-context metadata; never a host attestation or bypass."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "scripts/inspect-k2-observer-namespace-readonly.py"
)
spec = importlib.util.spec_from_file_location("k2_namespace_observer", SOURCE)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def proc(tmp_path):
    root = tmp_path / "proc"
    observer = root / "self"
    runtime = root / "123"
    for base in (observer, runtime):
        (base / "attr").mkdir(parents=True)
        (base / "status").write_text(
            "Name:\ttest\nUid:\t1000\t1000\t1000\t1000\n"
        )
        (base / "exe").symlink_to(tmp_path / "synthetic-executable")
    (observer / "uid_map").write_text("1000 1000 1\n")
    (runtime / "uid_map").write_text("0 4294967295 4294967295\n")
    (observer / "attr/current").write_text("unprivileged_userns (enforce)\n")
    (runtime / "attr/current").write_text("lm-studio (unconfined)\n")
    return root


def collect(proc, *, link_reader=None, inventory_reader=None):
    kwargs = {
        "proc_root": proc, "observer_real_uid": 1000,
        "inventory_reader": (
            (lambda _: {"root_pid": 123})
            if inventory_reader is None else inventory_reader
        ),
    }
    if link_reader is not None:
        kwargs["link_reader"] = link_reader
    return module.collect(**kwargs)


def test_different_userns_maps_and_confinement_reported_without_admission(proc):
    r = collect(proc)
    assert r["observer_user_namespace_map_observed"] is True
    assert r["runtime_user_namespace_map_observed"] is True
    assert r["observer_target_userns_maps_differ"] is True
    assert r["namespace_divergence_observed"] is True
    assert r["observer_uid_equal_runtime_uid"] is True
    assert r["observer_apparmor_mode"] == "ENFORCED_REPORTED"
    assert r["runtime_apparmor_mode"] == "UNCONFINED_REPORTED"
    assert r["observer_executable_link"] == "READABLE"
    assert r["runtime_executable_link"] == "READABLE"
    assert r["collection_status"] == "ISOLATION_CONTEXT_OBSERVED_NO_HOST_ATTESTATION"
    assert r["root_cause_proven"] is False
    for key in (
        "physical_process_socket_chain_verified",
        "actual_model_residency_verified",
        "independent_custody_verified",
        "canonical_operator_approval_verified",
        "can_dispatch",
    ):
        assert r[key] is False
    assert r["decision"] == "HOLD"
    assert "1000 1000 1" not in json.dumps(r)
    assert "4294967295" not in json.dumps(r)


def test_restricted_target_executable_still_reports_context_without_leak(proc):
    def deny(path):
        if path.parent.name == "123":
            raise PermissionError("sensitive process command /tmp/secret")
        return str(path)

    r = collect(proc, link_reader=deny)
    assert r["runtime_executable_link"] == "DENIED_OR_UNAVAILABLE"
    assert r["observer_executable_link"] == "READABLE"
    assert r["namespace_divergence_observed"] is True
    assert r["root_cause_proven"] is False
    assert "secret" not in json.dumps(r)
    assert r["can_dispatch"] is False


def test_same_userns_map_does_not_establish_host_or_key_custody(proc):
    (proc / "123/uid_map").write_text("1000 1000 1\n")
    r = collect(proc)
    assert r["observer_target_userns_maps_differ"] is False
    assert r["collection_status"] == "SAME_MAPPING_UNPROVEN_CUSTODY"
    assert r["root_cause_proven"] is False
    assert r["independent_custody_verified"] is False


def test_different_uid_fails_to_claim_same_uid(proc):
    (proc / "123/status").write_text(
        "Name:\ttest\nUid:\t2000\t2000\t2000\t2000\n"
    )
    r = collect(proc)
    assert r["observer_uid_equal_runtime_uid"] is False
    assert r["can_dispatch"] is False


@pytest.mark.parametrize(("profile", "expected"), [
    ("unprivileged_userns (enforce)", "ENFORCED_REPORTED"),
    ("lm-studio (unconfined)", "UNCONFINED_REPORTED"),
    ("unconfined", "UNCONFINED_REPORTED"),
    ("some_profile (complain)", "COMPLAIN_REPORTED"),
    ("other", "OTHER_OR_UNAVAILABLE"),
    ("", "UNKNOWN"),
])
def test_apparmor_labels_are_coarse_and_not_exposed(proc, profile, expected):
    p = proc / "self/attr/current"
    p.write_text(profile)
    assert module.apparmor_kind(p) == expected


@pytest.mark.parametrize("mapping", [
    "",
    "a b c\n",
    "1000 1000\n",
    "-1 1000 1\n",
    "1000 1000 0\n",
    "1000 1000 4294967299\n",
    "\n".join(["0 0 1"] * 9),
])
def test_malformed_namespace_mapping_denied(proc, mapping):
    (proc / "123/uid_map").write_text(mapping)
    r = collect(proc)
    assert r["collection_status"] == "FAIL_CLOSED"
    assert r["runtime_user_namespace_map_observed"] is False
    assert r["can_dispatch"] is False


def test_bad_runtime_identity_fails_closed(proc):
    r = collect(proc, inventory_reader=lambda _: {"root_pid": True})
    assert r["collection_status"] == "FAIL_CLOSED"
    assert r["can_dispatch"] is False


def test_missing_runtime_process_fails_closed(proc):
    r = collect(proc, inventory_reader=lambda _: {})
    assert r["collection_status"] == "FAIL_CLOSED"
    assert r["can_dispatch"] is False


def test_large_status_fails_closed_and_leaks_no_content(proc):
    (proc / "123/status").write_text(
        "Uid:\t1000\t1000\t1000\t1000\n" + "private data" * 2000
    )
    r = collect(proc)
    assert r["collection_status"] == "FAIL_CLOSED"
    assert "private data" not in json.dumps(r)


def test_missing_apparmor_label_cannot_be_claimed_verified(proc):
    (proc / "self/attr/current").unlink()
    r = collect(proc)
    assert r["collection_status"] == "FAIL_CLOSED"
    assert r["can_dispatch"] is False


def test_bad_status_uid_denied(proc):
    (proc / "123/status").write_text("Uid:\tnot-numeric\n")
    r = collect(proc)
    assert r["collection_status"] == "FAIL_CLOSED"
    assert r["observer_uid_equal_runtime_uid"] is None


def test_cli_is_always_nonzero_in_actual_runtime():
    proc = subprocess.run(
        ["python3", "-B", str(SOURCE)],
        text=True, capture_output=True, timeout=12, check=False,
    )
    assert proc.returncode == 2
    result = json.loads(proc.stdout)
    assert result["can_dispatch"] is False
    assert result["root_cause_proven"] is False
    assert result["decision"] == "HOLD"
    assert result["independent_custody_verified"] is False


def test_no_privilege_escalation_or_live_mutation():
    source = SOURCE.read_text()
    for forbidden in (
        "subprocess.run(", "os.setuid(", "os.setgid(", "os.chmod(",
        "os.chown(", "os.kill(", "ptrace(", "nsenter", "setns(",
        "tpm2_create", "systemctl restart", "CREATE TABLE",
    ):
        assert forbidden not in source
    assert not hasattr(module, "APPROVED_WITNESS_SIGNING_KEY")


def test_too_many_uid_map_lines_is_invalid(proc):
    (proc / "123/uid_map").write_text("0 0 1\n" * 15)
    assert collect(proc)["collection_status"] == "FAIL_CLOSED"


def test_nonexistent_target_executable_does_not_raise_into_user_report(proc):
    (proc / "123/exe").unlink()
    r = collect(proc)
    assert r["runtime_executable_link"] == "DENIED_OR_UNAVAILABLE"
    assert r["collection_status"] == "ISOLATION_CONTEXT_OBSERVED_NO_HOST_ATTESTATION"
    assert r["can_dispatch"] is False
