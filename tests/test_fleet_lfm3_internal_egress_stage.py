"""Synthetic offline network/identity denials for co-namespaced LFM3 staging.

No Docker network creation, no model requests, production API, keys or grants.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "_fleet_internal_egress_stage", SCRIPTS / "fleet_lfm3_internal_egress_stage.py"
)
assert SPEC and SPEC.loader
stage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stage
SPEC.loader.exec_module(stage)


def network(*, internal=True, driver="bridge", ingress=False, attachable=False, names=()):
    return {
        "Name": stage.NETWORK,
        "Driver": driver,
        "Internal": internal,
        "Ingress": ingress,
        "Attachable": attachable,
        "Containers": {str(i): {"Name": name} for i, name in enumerate(names)},
    }


def test_stage_network_requires_real_internal_docker_bridge(monkeypatch):
    fixture = network()
    monkeypatch.setattr(stage, "docker", lambda *args, **kwargs: json.dumps(fixture))
    assert stage._network_attest(expected_count=0, expected_containers=set()) == fixture


@pytest.mark.parametrize(
    "overrides",
    [
        {"Internal": False},
        {"Internal": None},
        {"Driver": "overlay"},
        {"Ingress": True},
        {"Attachable": True},
        {"Name": "unreviewed-production-network"},
        {"Containers": {"x": {"Name": "production-api"}}},
    ],
)
def test_invalid_network_identity_or_attached_container_denied(monkeypatch, overrides):
    fixture = {**network(), **overrides}
    monkeypatch.setattr(stage, "docker", lambda *_a, **_k: json.dumps(fixture))
    with pytest.raises(ValueError, match="network_(is_not_internal_and_private|attachment_census_changed)"):
        stage._network_attest(expected_count=0, expected_containers=set())


def test_exact_source_client_census_requires_no_unknown_containers(monkeypatch):
    fixture = network(names=(stage.MODEL_CONTAINER, stage.CLIENT_CONTAINER))
    monkeypatch.setattr(stage, "docker", lambda *_a, **_k: json.dumps(fixture))
    assert stage._network_attest(expected_count=2, expected_containers={stage.MODEL_CONTAINER, stage.CLIENT_CONTAINER})
    for requested in ({stage.MODEL_CONTAINER}, {stage.MODEL_CONTAINER, stage.GATEWAY_CONTAINER}, set()):
        with pytest.raises(ValueError, match="network_attachment_census_changed"):
            stage._network_attest(expected_count=2, expected_containers=requested)


@pytest.mark.parametrize(
    "identity",
    [
        {"unreviewed_network": {"IPAddress": "172.25.0.2"}},
        {stage.NETWORK: {"IPAddress": "8.8.8.8"}},
        {stage.NETWORK: {"IPAddress": "127.0.0.1"}},
        {stage.NETWORK: {"IPAddress": "not-an-IP"}},
        {stage.NETWORK: {"IPAddress": "172.25.0.2"}, "production_network": {}},
    ],
)
def test_container_ip_must_have_only_intended_private_network(monkeypatch, identity):
    monkeypatch.setattr(stage, "docker", lambda *_a, **_k: json.dumps(identity))
    with pytest.raises(ValueError):
        stage._ip_for(stage.MODEL_CONTAINER)


def test_container_ip_private_only_on_intended_network(monkeypatch):
    monkeypatch.setattr(stage, "docker", lambda *_a, **_k: json.dumps({stage.NETWORK: {"IPAddress": "172.25.0.2"}}))
    assert stage._ip_for(stage.MODEL_CONTAINER) == "172.25.0.2"


def test_egress_denial_only_accepts_explicit_negative_witness(monkeypatch):
    calls = []
    monkeypatch.setattr(
        stage, "docker", lambda *args, **kwargs: calls.append(args) or "external_ipv4_tcp_egress=DENIED"
    )
    stage._deny_external_egress(stage.MODEL_CONTAINER)
    assert calls[0][0:3] == ("exec", stage.MODEL_CONTAINER, "python3")
    assert "1.1.1.1" in calls[0][-1] and "8.8.8.8" in calls[0][-1]
    monkeypatch.setattr(stage, "docker", lambda *_a, **_k: "no egress proof available")
    with pytest.raises(ValueError, match="network_egress_not_denied"):
        stage._deny_external_egress(stage.MODEL_CONTAINER)


def _inputs_mock(monkeypatch, tmp_path, *, image=True, existing=(), networks=()):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"exact-1.1GB-model-represented-by-synthetic-tiny-bytes")
    monkeypatch.setattr(stage, "MODEL_FILE", str(model))
    monkeypatch.setattr(stage, "GGUF_SHA256", hashlib.sha256(model.read_bytes()).hexdigest())
    monkeypatch.setattr(stage.os, "geteuid", lambda: 1000)

    def checked_docker(*args, **kwargs):
        if args[:2] == ("image", "inspect"):
            return stage.IMAGE if image else "sha256:unexpected"
        if args[:2] == ("ps", "-a"):
            return "\n".join(existing)
        if args[:2] == ("network", "ls"):
            return "\n".join(networks)
        pytest.fail("inputs preflight tried to start service or modify state")

    monkeypatch.setattr(stage, "docker", checked_docker)
    return model


def test_exact_immutable_image_and_model_sha256_preflight_passes(monkeypatch, tmp_path):
    _inputs_mock(monkeypatch, tmp_path)
    stage._attested_local_inputs()


def test_drifted_model_bytes_denied_before_any_network(monkeypatch, tmp_path):
    model = _inputs_mock(monkeypatch, tmp_path)
    model.write_bytes(b"unreviewed model changed")
    with pytest.raises(ValueError, match="model_bytes_not_canonical"):
        stage._attested_local_inputs()


def test_drifted_image_denied_before_any_network(monkeypatch, tmp_path):
    _inputs_mock(monkeypatch, tmp_path, image=False)
    with pytest.raises(ValueError, match="immutable_cached_image_mismatch"):
        stage._attested_local_inputs()


@pytest.mark.parametrize("name", [stage.MODEL_CONTAINER, stage.GATEWAY_CONTAINER, stage.CLIENT_CONTAINER])
def test_refuses_existing_named_stage_containers(monkeypatch, tmp_path, name):
    _inputs_mock(monkeypatch, tmp_path, existing=(name,))
    with pytest.raises(ValueError, match="operator_stage_name_occupied"):
        stage._attested_local_inputs()


def test_refuses_existing_staging_network(monkeypatch, tmp_path):
    _inputs_mock(monkeypatch, tmp_path, networks=(stage.NETWORK,))
    with pytest.raises(ValueError, match="stage_network_name_occupied"):
        stage._attested_local_inputs()


def test_root_rejected_before_any_docker_calls(monkeypatch):
    monkeypatch.setattr(stage.os, "geteuid", lambda: 0)
    monkeypatch.setattr(stage, "docker", lambda *_a, **_k: pytest.fail("never call docker when root"))
    with pytest.raises(ValueError, match="nonroot_operator_required"):
        stage._attested_local_inputs()


def test_cleanup_names_are_bounded_to_staging_resources(monkeypatch):
    seen = []
    monkeypatch.setattr(
        stage.subprocess, "run", lambda args, **kwargs: seen.append(args) or SimpleNamespace(returncode=0)
    )
    stage._cleanup()
    assert seen == [
        ["docker", "rm", "-f", stage.GATEWAY_CONTAINER],
        ["docker", "rm", "-f", stage.CLIENT_CONTAINER],
        ["docker", "rm", "-f", stage.MODEL_CONTAINER],
        ["docker", "network", "rm", stage.NETWORK],
    ]
    assert "assistx-api" not in str(seen) and "neo4j" not in str(seen)


def test_fixed_stage_model_gateway_client_names_are_distinct():
    assert len({stage.MODEL_CONTAINER, stage.GATEWAY_CONTAINER, stage.CLIENT_CONTAINER}) == 3
    assert "production" not in stage.NETWORK
    assert len(stage.IMAGE) == 71 and stage.IMAGE.startswith("sha256:")
    assert len(stage.GGUF_SHA256) == 64
    assert stage.REPO.is_absolute()


def test_cli_no_live_has_zero_docker_or_network_effects(monkeypatch, capsys):
    monkeypatch.setattr(stage, "run", lambda: pytest.fail("default must never perform effects"))
    assert stage.main([]) == 2
    d = json.loads(capsys.readouterr().out)
    assert d["task_admission"] == "HOLD" and d["reason"] == "EXPLICIT_INTERNAL_STAGE_ONLY"


def test_cli_synthetic_positive_is_still_nonadmitting(monkeypatch, capsys):
    monkeypatch.setattr(
        stage, "run", lambda: {"physical_stage": "PASS", "task_admission": "HOLD", "approved_parallel_slots": 0}
    )
    assert stage.main(["--live"]) == 2
    d = json.loads(capsys.readouterr().out)
    assert d["task_admission"] == "HOLD" and d["approved_parallel_slots"] == 0


def test_cli_failed_proof_is_hold_and_restores_sigterm(monkeypatch, capsys):
    old = stage.signal.getsignal(stage.signal.SIGTERM)
    monkeypatch.setattr(stage, "run", lambda: (_ for _ in ()).throw(ValueError("simulated failure")))
    assert stage.main(["--live"]) == 2
    assert json.loads(capsys.readouterr().out)["physical_stage"] == "HOLD"
    assert stage.signal.getsignal(stage.signal.SIGTERM) == old


def test_termination_signal_requests_cleanup():
    with pytest.raises(ValueError, match="operator_stage_termination_requested"):
        stage._termination_requested(15, None)
