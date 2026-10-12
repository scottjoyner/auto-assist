import importlib.util
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "scripts" / "verify_private_fleet_trace_serve.py"
spec = importlib.util.spec_from_file_location("verify_trace_serve", source)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def example(*, port=8447, public=False):
    host = "fleet.tailnet.ts.net"
    key = f"{host}:{port}"
    return {
        "Web": {key: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8794"}}}},
        "AllowFunnel": {key: public}
    }


def test_empty_config_allows_private_new_port_without_mutating():
    result = audit_module.audit({"Web": {}, "AllowFunnel": {}},
                                dns_name="fleet.tailnet.ts.net", port=8447, target_port=8794)
    assert result["safe_to_configure_private_serve"] is True
    assert not result["existing_expected_mapping"]


def test_existing_correct_private_mapping_is_idempotent():
    result = audit_module.audit(example(), dns_name="fleet.tailnet.ts.net",
                                port=8447, target_port=8794)
    assert result["safe_to_configure_private_serve"] is True
    assert result["existing_expected_mapping"] is True


def test_public_funnel_on_target_is_denied_even_if_proxy_is_right():
    result = audit_module.audit(example(public=True), dns_name="fleet.tailnet.ts.net",
                                port=8447, target_port=8794)
    assert result["funnel_on_target"] is True
    assert result["safe_to_configure_private_serve"] is False


def test_existing_service_conflict_refused():
    config = example()
    config["Web"]["fleet.tailnet.ts.net:8447"]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:8123"
    result = audit_module.audit(config, dns_name="fleet.tailnet.ts.net",
                                port=8447, target_port=8794)
    assert result["port_occupied_by_other_service"] is True
    assert result["safe_to_configure_private_serve"] is False


def test_trace_already_exposed_on_another_public_port_is_denied():
    config = example()
    config["Web"]["fleet.tailnet.ts.net:8446"] = {
        "Handlers": {"/v1": {"Proxy": "http://127.0.0.1:8794/"}}
    }
    config["AllowFunnel"]["fleet.tailnet.ts.net:8446"] = True
    result = audit_module.audit(config, dns_name="fleet.tailnet.ts.net",
                                port=8447, target_port=8794)
    assert result["existing_public_trace_mapping"] is True
    assert result["safe_to_configure_private_serve"] is False
