"""Read-only fleet witness candidate telemetry is never authorization."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPT = (Path(__file__).resolve().parents[1] / "scripts"
          / "inspect-k2-witness-custody-readiness-readonly.py")
spec = importlib.util.spec_from_file_location("k2_custody_probe", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CHALLENGE = "a1" * 16
NOW = 1_800_000_000.0


def sample(**changes):
    data = {
        "challenge": CHALLENGE,
        "python": "3.12.1",
        "sqlite": "3.50.4",
        "tpm0": True,
        "tpmrm0": True,
        "tpm_pcr_sysfs_present": True,
        "tpm2_getcap_present": True,
        "ntp_synchronized": True,
        "secure_boot_variables_visible": True,
        "kernel_lockdown_file_visible": True,
        "clock_unix": NOW,
    }
    data.update(changes)
    return data


def probe(report=None, *, alias="xwing", code=0):
    received = []
    data = sample() if report is None else report

    def runner(command, **kwargs):
        received.append((command, kwargs))
        return SimpleNamespace(returncode=code, stdout=json.dumps(data), stderr="private")

    stamp = iter([NOW, NOW + 0.1])
    tick = iter([10.0, 10.1])
    result = module.probe(
        alias, runner=runner,
        nonce_source=lambda n: CHALLENGE if n == 16 else "wrong",
        wall_clock=lambda: next(stamp),
        monotonic=lambda: next(tick),
    )
    return result, received


def test_technically_ready_never_becomes_independent():
    d, calls = probe()
    assert d["status"] == "TPM_TECHNICAL_CANDIDATE_ONLY"
    assert d["tpm_device_visible"] is True
    assert d["tpm2_tool_available"] is True
    assert d["secure_boot_status_verified"] is False
    assert d["tpm_quote_verified"] is False
    assert d["independent_administration_verified"] is False
    assert d["independent_key_custody_verified"] is False
    assert d["external_antirollback_verified"] is False
    assert d["can_dispatch"] is False
    assert d["decision"] == "HOLD"
    assert len(calls) == 1


def test_shared_fleet_ssh_is_strict_no_forwarding():
    _, calls = probe()
    command, options = calls[0]
    joined = " ".join(command)
    for option in (
        "BatchMode=yes", "StrictHostKeyChecking=yes",
        "ForwardAgent=no", "ClearAllForwardings=yes",
        "ControlMaster=no", "ControlPath=none",
        "UpdateHostKeys=no", "PermitLocalCommand=no",
    ):
        assert option in joined
    assert command[-2] == "xwing"
    assert "python3 -c" in command[-1]
    assert options["timeout"] == 9
    assert options["check"] is False
    assert "sudo" not in command[-1]
    assert "tpm2_create" not in command[-1]


@pytest.mark.parametrize(("changes", "expected"), [
    ({"tpm0": False, "tpmrm0": False, "tpm_pcr_sysfs_present": False},
     "NO_VISIBLE_TPM_ALTERNATE_CUSTODY_REQUIRED"),
    ({"tpm2_getcap_present": False}, "TPM_TOOLING_NOT_VALIDATED"),
    ({"ntp_synchronized": False}, "CLOCK_SYNC_HOLD"),
    ({"python": "3.9.20"}, "RUNTIME_CAPABILITY_HOLD"),
    ({"sqlite": "3.31.1"}, "RUNTIME_CAPABILITY_HOLD"),
    ({"clock_unix": NOW + 13}, "CLOCK_SKEW_HOLD"),
])
def test_capability_limits_never_admit(changes, expected):
    d, _ = probe(sample(**changes))
    assert d["status"] == expected
    assert d["can_dispatch"] is False


@pytest.mark.parametrize("alias", ["evil;rm", "../destroyer", "-oProxyCommand=x", "none", ""])
def test_unreviewed_hosts_refused_before_ssh(alias):
    d, calls = probe(alias=alias)
    assert d["status"] == "FAIL_CLOSED"
    assert d["observed"] is False
    assert calls == []


def test_unknown_stale_remote_challenge_denied():
    d, _ = probe(sample(challenge="f" * 32))
    assert d["status"] == "FAIL_CLOSED"


def test_duplicate_json_key_denied():
    raw = json.dumps(sample()).replace('"tpm0": true', '"tpm0": true, "tpm0": false')
    with pytest.raises(ValueError, match="duplicate_remote_json_key"):
        module.parse_report(raw, challenge=CHALLENGE)


@pytest.mark.parametrize(("changes", "reason"), [
    ({"tpm0": "true"}, "invalid_remote_capability_type"),
    ({"ntp_synchronized": 1}, "invalid_remote_capability_type"),
    ({"python": "3.12-dev"}, "invalid_remote_versions"),
    ({"clock_unix": True}, "invalid_remote_clock"),
    ({"clock_unix": 0}, "invalid_remote_clock"),
])
def test_malformed_remote_data_denied(changes, reason):
    with pytest.raises(ValueError, match=reason):
        module.parse_report(json.dumps(sample(**changes)), challenge=CHALLENGE)


def test_nonfinite_remote_json_denied():
    raw = json.dumps(sample()).replace('"clock_unix": 1800000000.0',
                                      '"clock_unix": NaN')
    with pytest.raises(ValueError, match="nonfinite_remote_value"):
        module.parse_report(raw, challenge=CHALLENGE)


def test_remote_claiming_custody_approval_is_untrusted_extra_field():
    with pytest.raises(ValueError, match="unknown_or_missing_remote_fields"):
        module.parse_report(
            json.dumps(sample(independent_key_custody_verified=True)),
            challenge=CHALLENGE,
        )


def test_remote_claiming_dispatch_authority_is_untrusted_extra_field():
    with pytest.raises(ValueError, match="unknown_or_missing_remote_fields"):
        module.parse_report(json.dumps(sample(can_dispatch=True)), challenge=CHALLENGE)


def test_oversized_remote_output_denied():
    with pytest.raises(ValueError, match="missing_or_oversized_remote_data"):
        module.parse_report("A" * 2050, challenge=CHALLENGE)


def test_malformed_json_denied():
    with pytest.raises(ValueError, match="invalid_remote_json"):
        module.parse_report("{", challenge=CHALLENGE)


def test_host_key_ssh_failure_does_not_leak_stderr():
    d, _ = probe(code=255)
    assert d["status"] == "FAIL_CLOSED"
    assert "private" not in str(d)
    assert d["can_dispatch"] is False


def test_timeout_refuses_safely():
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=9, stderr="private")

    d = module.probe(
        "destroyer", runner=timeout, nonce_source=lambda _: CHALLENGE
    )
    assert d["status"] == "FAIL_CLOSED"
    assert "private" not in str(d)


def test_malformed_nonce_refused_before_ssh():
    called = []
    d = module.probe(
        "lenovo", runner=lambda *a, **k: called.append((a, k)),
        nonce_source=lambda _: "invalid",
    )
    assert d["status"] == "FAIL_CLOSED"
    assert not called


def test_visible_efi_variables_does_not_prove_secure_boot():
    d, _ = probe(sample(secure_boot_variables_visible=True))
    assert d["secure_boot_variables_visible"] is True
    assert d["secure_boot_status_verified"] is False


def test_tpm_without_pcr_sysfs_not_classed_as_visible():
    d, _ = probe(sample(tpm_pcr_sysfs_present=False))
    assert d["status"] == "NO_VISIBLE_TPM_ALTERNATE_CUSTODY_REQUIRED"


def test_main_returns_nonzero_even_when_every_remote_host_technically_ready(capsys):
    with patch.object(module, "probe", side_effect=[
        {"alias": a, "status": "TPM_TECHNICAL_CANDIDATE_ONLY",
         "tpm_device_visible": True, "can_dispatch": False} for a in module.HOST_ALIASES
    ]):
        exitcode = module.main(list(module.HOST_ALIASES))
    result = json.loads(capsys.readouterr().out)
    assert exitcode == 2
    assert result["decision"] == "NO_GO_RESEARCH_ONLY"
    for k in (
        "independent_administration_verified", "independent_key_custody_verified",
        "external_antirollback_verified", "production_trust_root_pinned",
        "can_dispatch",
    ):
        assert result[k] is False


def test_duplicate_alias_rejected_before_probe():
    with pytest.raises(SystemExit, match="unique reviewed host aliases"):
        module.main(["destroyer", "destroyer"])


def test_no_write_or_signer_provisioning_in_remote_source():
    code = module.REMOTE
    for forbidden in (
        "write_text(", "write_bytes(", "open('/dev/tpm", "tpm2_create",
        "tpm2_quote", "rm -", "chmod(", "chown(", "systemctl",
        "ssh-keygen", "Ed25519PrivateKey", "CREATE TABLE",
        "docker ", "git commit", "curl ", "wget ",
    ):
        assert forbidden not in code
    assert not hasattr(module, "APPROVED_OPERATOR_PRIVATE_KEY")
    assert not hasattr(module, "APPROVED_ISSUER_PRIVATE_KEY")
