"""Read-only fleet provenance audit: never expose env values or claim build trust."""
from __future__ import annotations

import copy
import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts import read_only_runtime_provenance as audit

SECRET = "not-a-real-secret-for-a-fake-identity"


def _container(name, user=SECRET, uri="bolt://synthetic-graph:7687"):
    env = [
        "NEO4J_USER=" + user,
        "NEO4J_URI=" + uri,
        "NEO4J_PASSWORD=" + SECRET,
        "JWT_SECRET=" + SECRET,
        "AUTO_ROUTER_ADMIN_TOKEN=" + SECRET,
    ]
    return {
        "Name": "/" + name,
        "Id": "f" * 64,
        "Image": "sha256:" + "a" * 64,
        "State": {"Status": "running", "StartedAt": "2026-10-17T15:00:00Z"},
        "Config": {
            "Env": env,
            "Labels": {"org.opencontainers.image.revision": "b" * 40},
        },
        "NetworkSettings": {"Networks": {"isolated-test": {}}},
    }


def _fixtures():
    return {name: _container(name) for name in audit.CONTAINERS}


def test_readonly_report_holds_and_does_not_leak_credentials():
    value = audit.build_report(_fixtures(), "synthetic")
    encoded = json.dumps(value)
    assert value["production_decision"] == "HOLD"
    assert value["promotion_authorized"] is False
    assert value["no_production_mutations_performed"] is True
    assert "SHARED_NEO4J_PRINCIPAL_REFERENCE" in value["findings"]
    assert "NO_VERIFIED_SOURCE_TO_IMAGE_ATTESTATION" in value["findings"]
    assert "G1" in encoded
    assert SECRET not in encoded
    assert "bolt://synthetic-graph" not in encoded
    for item in value["containers"]:
        assert item["image"]["source_to_image_verified"] is False
        assert item["image"]["claimed_source_sha"] == "b" * 40


def test_distinct_identities_detected_without_disclosing_actual_values():
    items = _fixtures()
    items["assistx-worker"] = _container("assistx-worker", user="different-synthetic")
    report = audit.build_report(items, "synthetic")
    assert "SHARED_NEO4J_PRINCIPAL_REFERENCE" not in report["findings"]
    assert "SHARED_NEO4J_ENDPOINT_REFERENCE" in report["findings"]
    assert "different-synthetic" not in json.dumps(report)


@pytest.mark.parametrize("corruption", [
    lambda a: a.pop("neo4j"),
    lambda a: a["assistx-api"].update({"Image": "latest"}),
    lambda a: a["assistx-worker"].update({"Name": "/production-db"}),
    lambda a: a["neo4j"].update({"State": {"Status": "hacked"}}),
    lambda a: a["assistx-api"]["Config"].update({"Env": ["NEO4J_USER=a", "NEO4J_USER=b"]}),
])
def test_missing_or_ambiguous_deployment_data_fails_closed(corruption):
    src = _fixtures()
    corruption(src)
    with pytest.raises(audit.EvidenceHold):
        audit.build_report(src, "synthetic")


def test_missing_principal_cannot_be_mistaken_for_separation():
    src = _fixtures()
    src["assistx-worker"]["Config"]["Env"] = ["NEO4J_URI=bolt://host"]
    report = audit.build_report(src, "synthetic")
    assert "MISSING_GRAPH_PRINCIPAL_IDENTITIES" in report["findings"]
    assert "SHARED_NEO4J_PRINCIPAL_REFERENCE" not in report["findings"]


def test_only_allowlisted_containers_can_be_inspected(monkeypatch):
    monkeypatch.setattr(
        audit.subprocess, "check_output",
        lambda *a, **kw: pytest.fail("never query unapproved Docker target"),
    )
    with pytest.raises(audit.EvidenceHold):
        audit._docker_inspect("production-secret-container")


def test_docker_execution_is_argv_only_and_inspect_only(monkeypatch):
    observed = []

    def mock_check_output(cmd, **kwargs):
        observed.append((cmd, kwargs))
        name = cmd[-1]
        return json.dumps([_container(name)])

    monkeypatch.setattr(audit.subprocess, "check_output", mock_check_output)
    report = audit.collect()
    assert report["promotion_authorized"] is False
    assert len(observed) == len(audit.CONTAINERS)
    for cmd, options in observed:
        assert cmd[:3] == ["docker", "container", "inspect"]
        assert cmd[-1] in audit.CONTAINERS
        assert options.get("timeout") == 8
        assert options.get("stderr") == subprocess.DEVNULL


def test_unsigned_label_never_becomes_attestation():
    src = _fixtures()
    src["assistx-api"]["Config"]["Labels"]["org.opencontainers.image.revision"] = "0" * 40
    report = audit.build_report(src, "synthetic")
    assert not report["containers"][0]["image"]["source_to_image_verified"]


def test_cli_reports_hold_not_traceback_for_unavailable_docker(monkeypatch, capsys):
    monkeypatch.setattr(audit, "collect", lambda: (_ for _ in ()).throw(audit.EvidenceHold("unavailable")))
    assert audit.main(["--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "production_decision": "HOLD",
        "error": "unavailable",
        "promotion_authorized": False,
    }
