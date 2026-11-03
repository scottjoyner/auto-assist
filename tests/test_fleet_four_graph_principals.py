"""Synthetic security regressions for pinned four-service Neo4j grants gate.

No Docker is executed. Mocked image identities, credential-free receipt values,
and direct source checks make the research gate fail closed in hosted CI.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import read_only_fleet_four_graph_principals as gate  # noqa: E402
import read_only_phonelog_graph_privileges as phone  # noqa: E402

_IMAGES = {
    "assistx-api": "sha256:" + "a" * 64,
    "assistx-worker": "sha256:" + "b" * 64,
    "assistx-hermes-adapter": "sha256:" + "c" * 64,
    "phonelog": "sha256:" + "d" * 64,
}
_ID = "f" * 64


def _phone_result(*, image=None, admin=True):
    return {
        "evidence_tier": "PRODUCTION_READ_ONLY_NATIVE_NEO4J",
        "container_id": _ID,
        "image_id": image or _IMAGES["phonelog"],
        "native_query_scope": "CURRENT_USER_PRIVILEGES_READ_ONLY",
        "granted_privilege_count": 19 if admin else 2,
        "wildcard_graph_grant_count": 18 if admin else 0,
        "write_action_count": 2 if admin else 0,
        "role_count": 2 if admin else 1,
        "has_admin_role": admin,
        "no_mutations": True,
        "production_decision": "HOLD",
        "promotion_authorized": False,
        "effective_privilege_cutover_verified": False,
    }


def _three_result():
    return {
        "production_decision": "HOLD",
        "read_only_native_evidence": True,
        "all_required_services_inspected": True,
        "roles_cutover_verified": False,
        "global_task_writer_authority_verified": False,
        "dispatch_authorized": False,
        "broad_admin_service_count": 3,
        "broad_write_service_count": 3,
        "wildcard_graph_service_count": 3,
        "services": [
            {
                "service": name, "image_pinned": True,
                "role_count": 2, "has_admin_role": True,
                "grant_count": 19,
                "wildcard_graph_grants": 18, "write_action_grants": 2,
            }
            for name in gate._NAMES[:3]
        ],
    }


def _patch(monkeypatch, phone_override=None, first_override=None):
    calls = []

    def old(*, pinned_images):
        calls.append(("three", dict(pinned_images)))
        return _three_result() if first_override is None else first_override

    def new(*, expected_image):
        calls.append(("phone", expected_image))
        return _phone_result() if phone_override is None else phone_override

    monkeypatch.setattr(gate.three, "census", old)
    monkeypatch.setattr(gate.phone, "collect", new)
    return calls


def test_all_four_native_probes_are_required_in_fixed_order(monkeypatch):
    calls = _patch(monkeypatch)
    r = gate.census(pinned_images=_IMAGES)
    assert calls == [
        ("three", {n: _IMAGES[n] for n in gate._NAMES[:3]}),
        ("phone", _IMAGES["phonelog"]),
    ]
    assert r["service_count"] == 4
    assert r["all_required_services_inspected"] is True
    assert r["broad_admin_service_count"] == 4
    assert r["broad_write_service_count"] == 4
    assert r["wildcard_graph_service_count"] == 4
    assert r["production_decision"] == "HOLD"
    assert r["dispatch_authorized"] is False
    assert r["roles_cutover_verified"] is False
    assert r["global_task_writer_authority_verified"] is False
    assert [x["service"] for x in r["services"]] == list(gate._NAMES)
    assert "container_id" not in str(r)
    assert "password" not in str(r).lower()


@pytest.mark.parametrize("bad", [
    {}, {n: x for n, x in _IMAGES.items() if n != "phonelog"},
    {**_IMAGES, "other": "sha256:" + "e" * 64},
    {**_IMAGES, "phonelog": "not-a-digest"},
    {**_IMAGES, "phonelog": None},
    {**_IMAGES, "phonelog": "sha256:" + "z" * 64},
    {**_IMAGES, "phonelog": True},
])
def test_missing_or_invalid_image_pins_refused_without_native_inspection(monkeypatch, bad):
    calls = _patch(monkeypatch)
    with pytest.raises(gate.FourPrincipalHold, match="all_four"):
        gate.census(pinned_images=bad)
    assert calls == []


@pytest.mark.parametrize("alter", [
    {"image_id": "sha256:" + "0" * 64},
    {"production_decision": "GO"},
    {"promotion_authorized": True},
    {"no_mutations": False},
    {"effective_privilege_cutover_verified": True},
    {"native_query_scope": "USER_SUPPLIED_QUERY"},
    {"unknown_secret": "sensitive"},
    {"role_count": -1},
    {"has_admin_role": 1},
    {"write_action_count": 200},
])
def test_tampered_phone_receipt_refused(monkeypatch, alter):
    _patch(monkeypatch, phone_override={**_phone_result(), **alter})
    with pytest.raises(gate.FourPrincipalHold):
        gate.census(pinned_images=_IMAGES)


def test_phonelog_unavailable_cannot_promote_first_three(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(gate.phone, "collect", lambda **kw: (
        _ for _ in ()).throw(phone.PhoneCensusHold("native_phone_probe_failed")
    ))
    with pytest.raises(gate.FourPrincipalHold, match="native_image_pinned"):
        gate.census(pinned_images=_IMAGES)


def test_forged_first_three_go_receipt_is_rejected(monkeypatch):
    _patch(monkeypatch, first_override={**_three_result(), "production_decision": "GO"})
    with pytest.raises(gate.FourPrincipalHold, match="untrusted_three"):
        gate.census(pinned_images=_IMAGES)


def test_all_restricted_still_does_not_grant_dispatch(monkeypatch):
    first = _three_result()
    first["services"] = [
        {**s, "has_admin_role": False, "wildcard_graph_grants": 0,
         "write_action_grants": 0, "role_count": 1, "grant_count": 2}
        for s in first["services"]
    ]
    first.update(broad_admin_service_count=0, broad_write_service_count=0,
                 wildcard_graph_service_count=0)
    _patch(monkeypatch, first_override=first, phone_override=_phone_result(admin=False))
    result = gate.census(pinned_images=_IMAGES)
    assert result["broad_admin_service_count"] == 0
    assert result["production_decision"] == "HOLD"
    assert result["dispatch_authorized"] is False


def test_cli_always_exits_hold_and_redacts_native_error(monkeypatch, capsys):
    _patch(monkeypatch)
    argv = [
        "--expected-api-image", _IMAGES["assistx-api"],
        "--expected-worker-image", _IMAGES["assistx-worker"],
        "--expected-hermes-image", _IMAGES["assistx-hermes-adapter"],
        "--expected-phonelog-image", _IMAGES["phonelog"], "--json",
    ]
    assert gate.main(argv) == 3
    row = json.loads(capsys.readouterr().out)
    assert row["production_decision"] == "HOLD"
    monkeypatch.setattr(gate.phone, "collect", lambda **kw: (
        _ for _ in ()).throw(phone.PhoneCensusHold("private-password-XYZ")
    ))
    assert gate.main(argv) == 3
    text = capsys.readouterr().out
    assert "private-password" not in text
    assert json.loads(text)["all_required_services_inspected"] is False


def test_phone_native_image_and_running_container_pinned_before_and_after(monkeypatch):
    received = []
    def docker(*args):
        received.append(args)
        if args[:2] == ("container", "inspect"):
            return json.dumps([{
                "Id": _ID, "Name": "/phonelog", "Image": _IMAGES["phonelog"],
                "State": {"Status": "running"},
            }])
        assert args[:4] == ("exec", "-i", "phonelog", "python3")
        return json.dumps({
            k: v for k, v in _phone_result().items()
            if k in {
                "native_query_scope", "granted_privilege_count",
                "wildcard_graph_grant_count", "write_action_count",
                "role_count", "has_admin_role", "no_mutations",
            }
        })
    monkeypatch.setattr(phone, "_docker", docker)
    x = phone.collect(expected_image=_IMAGES["phonelog"])
    assert x["has_admin_role"] is True
    assert [call[:2] for call in received] == [
        ("container", "inspect"), ("exec", "-i"), ("container", "inspect")
    ]
    assert all("SHOW " in call[-1] for call in received if call[:2] == ("exec", "-i"))


@pytest.mark.parametrize("changed", [
    {"Image": "sha256:" + "0" * 64},
    {"Id": "0" * 64},
    {"State": {"Status": "exited"}},
])
def test_phone_runtime_drift_denied(monkeypatch, changed):
    count = 0
    def docker(*args):
        nonlocal count
        if args[:2] == ("container", "inspect"):
            count += 1
            base = {"Id": _ID, "Name": "/phonelog",
                    "Image": _IMAGES["phonelog"], "State": {"Status": "running"}}
            if count > 1:
                base.update(changed)
            return json.dumps([base])
        return json.dumps({
            k: v for k, v in _phone_result().items()
            if k in {
                "native_query_scope", "granted_privilege_count",
                "wildcard_graph_grant_count", "write_action_count",
                "role_count", "has_admin_role", "no_mutations",
            }
        })
    monkeypatch.setattr(phone, "_docker", docker)
    with pytest.raises(phone.PhoneCensusHold):
        phone.collect(expected_image=_IMAGES["phonelog"])


def test_no_migration_or_dynamic_queries_in_research_gate():
    root = Path(__file__).resolve().parents[1] / "scripts"
    for p in (
        root / "read_only_phonelog_graph_privileges.py",
        root / "read_only_fleet_four_graph_principals.py",
    ):
        source = p.read_text()
        assert "SHOW USER PRIVILEGES" in source or "three.census" in source
        assert "GRANT ROLE " not in source
        assert "CREATE USER " not in source
        assert "execute_write(" not in source
        assert "docker run " not in source
        assert "NEO4J_PASSWORD" not in source or p.name.startswith("read_only_phonelog")
