import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "scripts/fleet-runtime-admission-preflight.py"
spec = importlib.util.spec_from_file_location("admission_preflight", path)
assert spec and spec.loader
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def target():
    return {
        "node_id": "x1-370",
        "expected_model": "k2-horizon-0.9b",
        "access_paths": {
            "lan": "http://192.168.1.237:1234/v1",
            "tailscale": "http://100.64.43.123:1234/v1",
        },
    }


def test_only_private_dual_paths():
    with pytest.raises(preflight.UnsafeEndpoint):
        preflight.private_url("http://8.8.8.8:1234/v1", transport="lan")
    with pytest.raises(preflight.UnsafeEndpoint):
        preflight.private_url("http://192.168.1.237:1234/v1", transport="tailscale")
    with pytest.raises(preflight.UnsafeEndpoint):
        preflight.private_url("http://100.64.43.123:1234/v1", transport="lan")
    assert preflight.private_url("http://100.64.43.123:1234/v1", transport="tailscale")


def test_visible_only_never_admission(monkeypatch):
    monkeypatch.setattr(preflight, "get_json", lambda url, timeout: {"data": [{"id": "k2-horizon-0.9b"}]})
    r = preflight.build_report([target()])
    assert r["api_visible_count"] == 1
    assert r["admission_applied"] is False
    assert r["not_an_approval"] is True
    assert r["nodes"][0]["physical_attestation"] is False


def test_mismatch_and_timeout_block(monkeypatch):
    monkeypatch.setattr(preflight, "get_json", lambda url, timeout: {"data": [{"id": "other"}]})
    assert preflight.build_report([target()])["blocked_count"] == 1

    def fail(url, timeout):
        raise TimeoutError()

    monkeypatch.setattr(preflight, "get_json", fail)
    assert preflight.build_report([target()])["blocked_count"] == 1


def test_duplicate_and_missing_path_fail():
    with pytest.raises(ValueError, match="duplicate"):
        preflight.build_report([target(), target()])
    t = target()
    del t["access_paths"]["lan"]
    assert preflight.probe_target(t)["status"] == "blocked"


def test_redirect_rejected():
    with pytest.raises(preflight.UnsafeEndpoint, match="redirects"):
        preflight.NoRedirect().redirect_request(None, None, 302, "", {}, "http://8.8.8.8")


def test_missing_port_rejected():
    with pytest.raises(preflight.UnsafeEndpoint, match="port"):
        preflight.private_url("http://192.168.1.237/v1", transport="lan")


def test_redirect_handler_cannot_follow_public_location():
    with pytest.raises(preflight.UnsafeEndpoint, match="redirects"):
        preflight.NoRedirect().redirect_request(None, None, 302, "", {}, "http://8.8.8.8")



def test_observer_reports_actual_model_id_but_never_admits(monkeypatch):
    monkeypatch.setattr(
        preflight, "get_json",
        lambda url, timeout: {"data": [{"id": "recursivesi/flint"}]},
    )
    report = preflight.build_report([target()])
    assert report["blocked_count"] == 1
    assert report["admission_applied"] is False
    assert report["not_an_approval"] is True
    node = report["nodes"][0]
    assert node["operator_approved"] is False
    assert node["physical_attestation"] is False
    assert node["paths"]["lan"]["advertised_model_count"] == 1
    assert node["paths"]["lan"]["advertised_model_ids"] == ["recursivesi/flint"]
    assert node["paths"]["tailscale"]["advertised_model_ids"] == ["recursivesi/flint"]


def test_observed_ids_bounded_and_match_uses_entire_list(monkeypatch):
    ids = [f"model-{i:02}" for i in range(20)]
    ids += ["k2-horizon-0.9b", "control\nsequence", "x" * 200]
    monkeypatch.setattr(
        preflight, "get_json",
        lambda url, timeout: {"data": [{"id": item} for item in ids]},
    )
    report = preflight.build_report([target()])
    assert report["api_visible_count"] == 1
    entry = report["nodes"][0]["paths"]["lan"]
    assert entry["model_advertised"] is True
    assert entry["advertised_model_count"] == len(ids)
    assert len(entry["advertised_model_ids"]) == 8
    assert entry["advertised_model_ids_truncated"] is True
    assert all(len(value) <= 160 and "\n" not in value
               for value in entry["advertised_model_ids"])


def test_malformed_model_list_fails_closed(monkeypatch):
    monkeypatch.setattr(
        preflight, "get_json",
        lambda url, timeout: {"data": "not-an-array"},
    )
    report = preflight.build_report([target()])
    assert report["blocked_count"] == 1
    assert report["nodes"][0]["paths"]["lan"]["reachable"] is False
    assert report["nodes"][0]["operator_approved"] is False