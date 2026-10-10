"""Offline proof contracts for bounded, read-only Tailnet model observation."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reconciliation-probe-tailnet-models.py"
SPEC = importlib.util.spec_from_file_location("tailnet_model_witness", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def inventory():
    return {"authority": "candidate_reachability_only", "nodes": [
        {"node_id": "x1", "online": True, "tailscale_ips": ["100.64.43.123"]},
        {"node_id": "xwing", "online": True, "tailscale_ips": ["100.108.99.47"]},
        {"node_id": "offline", "online": False, "tailscale_ips": ["100.83.1.1"]},
        {"node_id": "external", "online": True, "tailscale_ips": ["8.8.8.8"]},
        {"node_id": "funnel", "online": True, "tailscale_ips": ["fd7a:115c:a1e0::1"]},
    ]}


def reply(mapping):
    def fetch(url, timeout):
        code, obj = mapping.get(url, (0, {}))
        return code, json.dumps(obj).encode() if code == 200 else b""
    return fetch
def test_bounded_targets_never_probe_offline_public_or_funnel():
    targets, coverage = module.plan_targets(inventory(), [1234, 1235], {"xwing": [9988]})
    assert len(targets) == 5
    assert set(x["ip"] for x in targets) == {"100.64.43.123", "100.108.99.47"}
    assert {x["port"] for x in targets if x["node"] == "xwing"} == {1234, 1235, 9988}
    assert coverage["skipped_over_capacity"] == 0


def test_no_admission_even_when_lmstudio_reports_resident():
    base = "http://100.64.43.123:1234"
    fetch = reply({base + "/api/v1/models": (200, {"models": [
        {"key": "alpha", "type": "llm", "loaded_instances": [{"id": "a"}]},
        {"key": "cold", "type": "llm", "loaded_instances": []},
        {"key": "embed", "type": "embedding", "loaded_instances": [{"id": "e"}]}
    ]})})
    result = module.inspect_target({"node": "x1", "ip": "100.64.43.123", "port": 1234}, fetch)
    assert result["state"] == "resident_verified"
    assert [(x["id"], x["state"]) for x in result["models"]] == [
        ("alpha", "resident_verified"), ("cold", "installed_not_loaded")]
    assert result["admitted"] is False
    assert [x["path"] for x in result["trace"]] == ["/api/v1/models"]
def test_openai_advertisement_is_not_loaded_proof():
    base = "http://100.64.43.123:1234"
    fetch = reply({
        base + "/v1/models": (200, {"data": [{"id": "k2"}]}),
        base + "/health": (200, {"status": "loading model"}),
        base + "/props": (200, {"model_alias": "k2", "model_path": "/models/k2.gguf", "total_slots": 4})
    })
    result = module.inspect_target({"node": "x1", "ip": "100.64.43.123", "port": 1234}, fetch)
    assert result["state"] == "advertised_unverified"
    assert all(x["state"] == "advertised_unverified" for x in result["models"])


def test_exact_llamacpp_health_props_proves_one_resident_model():
    base = "http://100.64.43.123:1234"
    fetch = reply({
        base + "/v1/models": (200, {"data": [{"id": "k2"}, {"id": "unverified"}]}),
        base + "/health": (200, {"status": "ok"}),
        base + "/props": (200, {"model_alias": "k2", "model_path": "/models/k2.gguf", "total_slots": 4})
    })
    result = module.inspect_target({"node": "x1", "ip": "100.64.43.123", "port": 1234}, fetch)
    assert result["state"] == "resident_verified"
    assert [(x["id"], x["state"]) for x in result["models"]] == [
        ("k2", "resident_verified"), ("unverified", "advertised_unverified")]
    assert result["admitted"] is False
def test_auth_errors_and_unreachable_are_distinct():
    target = {"node": "x1", "ip": "100.64.43.123", "port": 1234}
    base = "http://100.64.43.123:1234"
    assert module.inspect_target(target, reply({base + "/api/v1/models": (401, {})}))["state"] == "auth_required"
    assert module.inspect_target(target, reply({}))["state"] == "unreachable"
    assert module.inspect_target(target, reply({base + "/v1/models": (500, {})}))["state"] == "incompatible_or_degraded"


def test_forged_candidates_cannot_add_public_targets():
    bad = inventory()
    bad["nodes"].append({"node_id": "bad-ip", "online": True, "tailscale_ips": ["not-ip"]})
    targets, _ = module.plan_targets(bad, [1234])
    assert len(targets) == 2
    with pytest.raises(ValueError):
        module.plan_targets({"authority": "production_admitted", "nodes": []}, [1234])


def test_deterministic_order_and_source_custody():
    result = module.collect(inventory(), [1234], fetch=reply({}), workers=2)
    assert result["authority"] == "observational_only_not_runtime_admission"
    assert result["coverage"]["targets"] == 2
    assert [x["node"] for x in result["observations"]] == ["x1", "xwing"]
    assert len(result["source_inventory_sha256"]) == 64
    assert all(x["admitted"] is False for x in result["observations"])

def test_readonly_http_client_does_not_follow_redirects():
    """A Tailnet peer cannot expand a read-only probe to a second URL."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            if self.path.startswith("/redirect/"):
                code = int(self.path.rsplit("/", 1)[-1])
                self.send_response(code)
                self.send_header("Location", "/private-internal-service")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"should never be fetched")

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for code in (301, 302, 303, 307, 308):
                status, body = module.fetch_readonly(
                    f"http://127.0.0.1:{server.server_port}/redirect/{code}", 2.0)
                assert (status, body) == (code, b"")
        finally:
            server.shutdown()
            thread.join(timeout=2)
    assert received == [f"/redirect/{code}" for code in (301, 302, 303, 307, 308)]
