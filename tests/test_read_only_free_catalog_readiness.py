"""Contract for a no-network, non-authorizing OpenCode provider projection."""
import importlib.util
from pathlib import Path
import sys
import json

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/read_only_free_catalog_readiness.py"
spec = importlib.util.spec_from_file_location("catalog_readiness_fixture", PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def fixture(**overrides):
    d = {
        "all": [
            {"id": "zai", "models": {
                "glm-4.7-flash": {"status": "active",
                                  "cost": {"input": 0, "output": "0.00"}},
            }},
            {"id": "cohere", "models": {
                "north-mini-code-1-0": {"status": "active",
                                         "cost": {"input": 0, "output": 0}},
            }},
            {"id": "openrouter", "models": {
                "thinkingmachines/inkling-small:free": {
                    "status": "active", "cost": {"input": 0, "output": 0}},
            }},
        ],
        "connected": ["zai", "cohere", "openrouter"],
        "default": {"zai": "glm-4.7-flash"},
        "credential": "NOT_FOR_OUTPUT",
        "globalSecrets": {"apiKey": "DO_NOT_ECHO"},
    }
    d.update(overrides)
    return d


def by_route(result, provider, model):
    return next(row for row in result["records"]
                if row["provider"] == provider and row["model"] == model)


def test_catalog_is_not_live_authority():
    result = mod.catalog_readiness(fixture())
    assert result["dispatch_authorized"] is False
    assert result["independent_qualified_quota_groups"] == 0
    assert result["live_generation_calls"] == 0
    assert result["catalog_candidates"] == 3
    assert by_route(result, "zai", "glm-4.7-flash")["catalog_candidate"] is True
    assert by_route(result, "cohere", "north-mini-code-1-0")["dispatch_authorized"] is False
    assert by_route(result, "openrouter", "thinkingmachines/inkling-small:free")["reason"] == "catalog_only_unqualified"


def test_unknown_or_secret_payload_fields_are_never_redisclosed():
    d = fixture()
    d["all"][0]["models"]["glm-4.7-flash"]["providerSecret"] = "NEVER_LEAK_THIS_SECRET"
    s = json.dumps(mod.catalog_readiness(d))
    assert "DO_NOT_ECHO" not in s
    assert "NOT_FOR_OUTPUT" not in s
    assert "NEVER_LEAK_THIS_SECRET" not in s
    assert "apiKey" not in s
    assert "credential" not in s


def test_candidate_list_fixed_and_deterministic():
    d = fixture()
    a = mod.catalog_readiness(d)
    b = mod.catalog_readiness(d)
    assert a == b
    assert a["candidate_routes"] == sum(map(len, mod.CANDIDATES.values()))
    assert [r["provider"] + "/" + r["model"] for r in a["records"]] == sorted(
        r["provider"] + "/" + r["model"] for r in a["records"])


@pytest.mark.parametrize("value", [
    None, True, False, "NaN", "Infinity", float("nan"),
    float("inf"), "-0.01", "0.00000001", object()
])
def test_price_must_be_finite_literal_zero(value):
    assert mod.exact_zero(value) is False


@pytest.mark.parametrize("value", [0, "0.00", 0.0, "-0.0", "0E-12"])
def test_valid_zero_literal(value):
    assert mod.exact_zero(value) is True


def test_provider_not_connected_fails_closed():
    result = mod.catalog_readiness(fixture(connected=["zai"]))
    row = by_route(result, "cohere", "north-mini-code-1-0")
    assert row["reason"] == "provider_not_connected"
    assert row["catalog_candidate"] is False


def test_provider_connected_without_model_fails_closed():
    data = fixture()
    data["all"][0]["models"].clear()
    result = mod.catalog_readiness(data)
    assert by_route(result, "zai", "glm-4.7-flash")["reason"] == "model_missing"


def test_missing_price_does_not_count_as_free():
    data = fixture()
    data["all"][1]["models"]["north-mini-code-1-0"]["cost"] = {}
    row = by_route(mod.catalog_readiness(data), "cohere", "north-mini-code-1-0")
    assert row["reason"] == "cost_missing_or_nonzero"
    assert row["advertised_zero_input_output"] is False


@pytest.mark.parametrize("status", ["deprecated", "preview", "ACTIVE", None, True])
def test_unknown_model_status_is_not_active(status):
    data = fixture()
    data["all"][0]["models"]["glm-4.7-flash"]["status"] = status
    row = by_route(mod.catalog_readiness(data), "zai", "glm-4.7-flash")
    assert row["reason"] == "inactive_or_unknown"


def test_aliases_never_exact_qualify():
    for model in ["openrouter/free", "kilo/auto", "router",
                  "x//model", "a\\b", "free", "provider/default",
                  " ", "model\nkey"]:
        assert mod.exact_identifier(model) is False


def test_unknown_models_and_providers_cannot_expand_allowlist():
    data = fixture()
    data["all"].append({"id": "fake_provider", "models": {
        "free-all-models": {"status": "active", "cost": {"input": 0, "output": 0}}
    }})
    data["connected"].append("fake_provider")
    d = mod.catalog_readiness(data)
    assert all(row["provider"] != "fake_provider" for row in d["records"])
    assert d["candidate_routes"] == sum(map(len, mod.CANDIDATES.values()))


def test_invalid_outer_schema_does_not_authorize():
    for value in [[], 1, "bad", False, None]:
        with pytest.raises(ValueError):
            mod.catalog_readiness(value)


def test_invalid_inner_schema_is_blocked():
    d = fixture()
    d["all"] = {"zai": {"models": {}}}
    assert mod.catalog_readiness(d)["catalog_candidates"] == 0
    d = fixture(connected={"zai": True})
    assert mod.catalog_readiness(d)["catalog_candidates"] == 0


def test_cli_offline_json_projection_does_not_print_secrets(tmp_path, capsys):
    data_path = tmp_path / "provider.json"
    data_path.write_text(json.dumps(fixture()))
    code = mod.main(["--input", str(data_path)])
    output = capsys.readouterr().out
    assert code == 0
    assert "DO_NOT_ECHO" not in output
    assert json.loads(output)["catalog_candidates"] == 3


def test_cli_enforces_size_limit_before_read(tmp_path, monkeypatch):
    src = tmp_path / "massive.json"
    src.write_bytes(b"0" * 101)
    monkeypatch.setattr(mod, "MAX_BYTES", 100)
    with pytest.raises(SystemExit):
        mod.main(["--input", str(src)])


def test_duplicate_provider_rows_fail_closed():
    d = fixture()
    d["all"].append(dict(d["all"][0]))
    result = mod.catalog_readiness(d)
    assert by_route(result, "zai", "glm-4.7-flash")["reason"] == "model_missing"
    assert not by_route(result, "zai", "glm-4.7-flash")["catalog_candidate"]


def test_three_provider_duplicates_remain_denied():
    d = fixture()
    d["all"].extend([dict(d["all"][0]), dict(d["all"][0])])
    result = mod.catalog_readiness(d)
    assert not by_route(result, "zai", "glm-4.7-flash")["catalog_candidate"]


def test_unhashable_provider_id_does_not_crash_or_gain_access():
    d = fixture()
    d["all"].append({"id": ["zai"], "models": {
        "glm-4.7-flash": {"status": "active", "cost": {"input": 0, "output": 0}}
    }})
    result = mod.catalog_readiness(d)
    assert result["catalog_candidates"] == 3


def test_witness_fields_never_copied_from_untrusted_model_metadata():
    d = fixture()
    d["all"][0]["models"]["glm-4.7-flash"].update({
        "qualification": "qualified",
        "dispatch_authorized": True,
        "independent_upstream_group": "legitimate-verifier",
        "generation_receipt": "SIGNED",
        "provider_calls": 999,
    })
    row = by_route(mod.catalog_readiness(d), "zai", "glm-4.7-flash")
    assert row["qualification"] == "unqualified"
    assert row["dispatch_authorized"] is False
    assert row["independent_upstream_group"] == "unverified"
    assert row["generation_receipt"] == "absent"
    assert row["provider_calls"] == 0
