"""Fail-closed recovery-runbook verification (round 58).

Hermetic: no network, subprocess, database, service or live configuration.
The executor's ``http``/``runner`` seams are in-memory stubs and all state is
confined to pytest's ``tmp_path`` (which lives on the private SSD scratch dir).
"""

from types import SimpleNamespace

from assistx.recovery_runbooks import RecoveryRunbookExecutor, build_runbook, sign_runbook

NODE = "node-r58"
KEY_ID = "r58-key"
SECRET = "r58-secret"


def _plan(verify_after):
    return {
        "action": "refresh_agent",
        "node_id": NODE,
        "verify_after": list(verify_after),
    }


def _runbook(proposal_id, verify_after, *, force=None):
    runbook = build_runbook(_plan(verify_after), proposal_id)
    if force is not None:
        runbook["verification"] = force
    return sign_runbook(
        runbook,
        key_id=KEY_ID,
        secret=SECRET,
        ttl_seconds=900,
        nonce=proposal_id.ljust(64, "n")[:64],
    )


def _executor(tmp_path, *, status=200, probes=None):
    def http(*_args, **_kwargs):
        if probes is not None:
            probes.append("probe")
        return status, {"data": []}

    return RecoveryRunbookExecutor(
        node_id=NODE,
        lmstudio_url="http://inference.invalid",
        state_dir=str(tmp_path),
        http=http,
        runner=lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr=""),
        sleeper=lambda _: None,
        env={"FLEET_RECOVERY_SERVICE_ALIASES": '{"fleet_agent":"fleet-agent.service"}'},
        verify_keys={KEY_ID: SECRET},
    )


def test_empty_verification_fails_closed_without_a_probe(tmp_path):
    probes = []
    result = _executor(tmp_path / "empty", probes=probes).execute(
        _runbook("proposal-empty", ["service_online"], force=[])
    )
    assert result["ok"] is False
    assert result["status"] == "verification_failed"
    assert result["reason"] == "no_measurable_verification"
    assert probes == []


def test_unmeasurable_only_verification_fails_closed(tmp_path):
    probes = []
    result = _executor(tmp_path / "unmeasurable", probes=probes).execute(
        _runbook("proposal-unmeasurable", ["model_loaded"])
    )
    assert result["ok"] is False
    assert result["reason"] == "no_measurable_verification"
    assert probes == []


def test_non_list_verification_fails_closed(tmp_path):
    probes = []
    result = _executor(tmp_path / "notlist", probes=probes).execute(
        _runbook("proposal-notlist", [], force="service_online")
    )
    assert result["ok"] is False
    assert result["reason"] == "no_measurable_verification"
    assert probes == []


def test_mixed_verification_still_measures_health(tmp_path):
    probes = []
    result = _executor(tmp_path / "mixed", probes=probes).execute(
        _runbook("proposal-mixed", ["model_loaded", "service_online"])
    )
    assert result["ok"] is True
    assert result["status"] == "verified"
    assert probes == ["probe"]


def test_measurable_verification_still_fails_on_unhealthy_probe(tmp_path):
    result = _executor(tmp_path / "unhealthy", status=503).execute(
        _runbook("proposal-unhealthy", ["service_online"])
    )
    assert result["ok"] is False
    assert result["status"] == "verification_failed"
    assert result["reason"] == "inference_probe_failed"


def test_verify_helper_rejects_unmeasurable_checks(tmp_path):
    executor = _executor(tmp_path / "helper")
    for verification in ([], ["model_loaded"], "service_online"):
        decision = executor._verify({"verification": verification})
        assert decision["ok"] is False, verification
        assert decision["reason"] == "no_measurable_verification", verification
