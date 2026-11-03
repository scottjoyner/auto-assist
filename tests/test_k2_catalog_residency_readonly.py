"""Isolated model inventory tests. No model load, network or inference."""
from __future__ import annotations

import importlib.util
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "scripts/inspect-k2-catalog-residency-readonly.py"
)
spec = importlib.util.spec_from_file_location("k2_catalog_residency", SOURCE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def native_model(*, key, architecture="k2-horizon", type="llm", instances=None):
    return {
        "key": key, "type": type,
        "architecture": architecture,
        "loaded_instances": [] if instances is None else instances,
    }


def native_fixture(*, k2_loaded=0, other_loaded=0):
    return {"models": [
        native_model(key="flint", architecture="flint", instances=[
            {"id": "flint_instance"}] if other_loaded else []),
        native_model(key="bonsai_27b", architecture="qwen35"),
        native_model(key="bonsai_27b_alt", architecture="qwen35"),
        native_model(key="k2_7b"),
        native_model(key="k2_mova_36b"),
        native_model(key="k2_1b", instances=[
            {"id": "k2-horizon-0.9b"}] if k2_loaded else []),
        native_model(key="k2_4b"),
        native_model(key="qwen38", architecture="qwen4exp"),
        native_model(key="qwen2", architecture="qwen2"),
        native_model(key="ornith", architecture="qwen35moe"),
        native_model(key="nomic", architecture=None, type="embedding"),
    ]}


def compat_fixture(*, k2=False, other=False):
    return {"object": "list", "data": (
        ([{"id": module.EXPECTED_MODEL_ID}] if k2 else []) +
        ([{"id": "unrelated_loaded_model"}] if other else [])
    )}


def run_fixture(*, native=None, lan=None, loopback=None, tailscale=None, fail=()):
    data = {
        module.NATIVE_URL: native_fixture() if native is None else native,
        module.COMPAT_PATHS["lan"]: compat_fixture() if lan is None else lan,
        module.COMPAT_PATHS["loopback"]: (
            compat_fixture() if loopback is None else loopback
        ),
        module.COMPAT_PATHS["tailscale"]: (
            compat_fixture() if tailscale is None else tailscale
        ),
    }
    called = []

    def fetch(url):
        called.append(url)
        if url in fail:
            raise TimeoutError("sensitive private token: should not leak")
        return data[url]

    result = module.collect(fetch=fetch)
    return result, called


def test_zero_loaded_even_with_eleven_downloaded_and_four_k2_models():
    r, calls = run_fixture()
    assert len(calls) == 4
    assert set(calls) == {module.NATIVE_URL, *module.COMPAT_PATHS.values()}
    assert r["status"] == "CATALOG_HEALTHY_ALL_MODELS_IDLE"
    assert r["all_catalogs_reachable"] is True
    assert r["native"] == {
        "available_model_entries": 11,
        "available_llm_entries": 10,
        "available_embedding_entries": 1,
        "available_k2_family_entries": 4,
        "loaded_instance_count": 0,
        "loaded_k2_instance_count": 0,
    }
    for path in r["private_paths"].values():
        assert path == {
            "loaded_model_ids": 0,
            "expected_k2_model_id_present": False,
        }
    assert r["api_reachable_is_not_inference_ready"] is True
    assert r["installed_artifact_is_not_running_model"] is True
    assert r["physical_model_residency_verified"] is False
    assert r["actual_generation_tokens_measured"] is False
    assert r["independent_witness_verified"] is False
    assert r["canonical_operator_approval_verified"] is False
    assert r["can_dispatch"] is False
    assert r["decision"] == "HOLD"


def test_corroborated_native_and_openai_loaded_claim_is_not_physical_proof():
    both = compat_fixture(k2=True)
    r, _ = run_fixture(
        native=native_fixture(k2_loaded=1),
        lan=both, loopback=both, tailscale=both,
    )
    assert r["native"]["loaded_k2_instance_count"] == 1
    assert r["status"] == "CATALOGS_INDICATE_K2_LOAD_NOT_PHYSICAL_PROOF"
    assert r["physical_model_residency_verified"] is False
    assert r["can_dispatch"] is False


def test_native_k2_loaded_without_matching_openai_model_on_all_paths_is_hold():
    both = compat_fixture(k2=True)
    r, _ = run_fixture(
        native=native_fixture(k2_loaded=1),
        lan=both, loopback=both,
        tailscale=compat_fixture(),
    )
    assert r["status"] == "INCONSISTENT_RESIDENCY_CATALOGS_HOLD"
    assert r["can_dispatch"] is False


def test_native_k2_loaded_only_is_inconsistent():
    r, _ = run_fixture(native=native_fixture(k2_loaded=1))
    assert r["status"] == "INCONSISTENT_RESIDENCY_CATALOGS_HOLD"


def test_openai_k2_loaded_without_native_k2_is_inconsistent():
    both = compat_fixture(k2=True)
    r, _ = run_fixture(lan=both, loopback=both, tailscale=both)
    assert r["status"] == "INCONSISTENT_RESIDENCY_CATALOGS_HOLD"


def test_other_loaded_but_k2_absent_is_not_k2_residency():
    other = compat_fixture(other=True)
    r, _ = run_fixture(
        native=native_fixture(other_loaded=1),
        lan=other, loopback=other, tailscale=other,
    )
    assert r["status"] == "EXPECTED_K2_NOT_RESIDENT"
    assert r["native"]["loaded_instance_count"] == 1
    assert r["native"]["loaded_k2_instance_count"] == 0
    assert r["can_dispatch"] is False


@pytest.mark.parametrize("which", [
    module.NATIVE_URL, module.COMPAT_PATHS["loopback"],
    module.COMPAT_PATHS["lan"], module.COMPAT_PATHS["tailscale"],
])
def test_any_missing_catalog_forces_hold_without_error_leak(which):
    result, _ = run_fixture(fail=(which,))
    assert result["all_catalogs_reachable"] is False
    assert result["status"] == "CATALOG_UNAVAILABLE_HOLD"
    assert result["can_dispatch"] is False
    assert "private token" not in json.dumps(result)
    assert "error_code" in (
        result["native"] if which == module.NATIVE_URL
        else result["private_paths"][
            next(k for k, v in module.COMPAT_PATHS.items() if v == which)
        ]
    )


@pytest.mark.parametrize(("obj", "reason"), [
    ({}, "native_catalog_schema_invalid"),
    ({"models": None}, "native_catalog_schema_invalid"),
    ({"models": [None]}, "native_model_entry_invalid"),
    ({"models": [native_model(key="a", instances={})]},
     "native_loaded_instances_invalid"),
    ({"models": [native_model(key="a", type="invalid")]},
     "native_model_type_invalid"),
    ({"models": [native_model(key="")]},
     "invalid_catalog_model_identifier"),
    ({"models": [native_model(key="a\nb")]},
     "invalid_catalog_model_identifier"),
    ({"models": [native_model(key="a"), native_model(key="a")]},
     "native_duplicate_model_key"),
    ({"models": [native_model(key="a", instances=[{"id": "i"}, {"id": "i"}])]},
     "native_duplicate_loaded_instance"),
    ({"models": [native_model(key="a", instances=[None])]},
     "native_instance_schema_invalid"),
])
def test_invalid_native_catalog_holds(obj, reason):
    with pytest.raises(module.CatalogHold, match=reason):
        module.native_summary(obj)


def test_cross_model_instance_duplicate_denied():
    src = {"models": [
        native_model(key="one", instances=[{"id": "duplicate"}]),
        native_model(key="two", instances=[{"id": "duplicate"}]),
    ]}
    with pytest.raises(module.CatalogHold, match="native_duplicate_loaded_instance"):
        module.native_summary(src)


def test_too_many_models_denied():
    with pytest.raises(module.CatalogHold, match="native_catalog_too_many_models"):
        module.native_summary({
            "models": [native_model(key=f"k{i}") for i in range(129)]
        })


def test_too_many_instances_denied():
    with pytest.raises(module.CatalogHold, match="native_loaded_instances_invalid"):
        module.native_summary({
            "models": [native_model(
                key="long", instances=[{"id": f"i{i}"} for i in range(65)]
            )]
        })


@pytest.mark.parametrize(("obj", "reason"), [
    ({}, "openai_catalog_schema_invalid"),
    ({"data": None}, "openai_catalog_schema_invalid"),
    ({"data": [False]}, "openai_model_entry_invalid"),
    ({"data": [{"id": 9}]}, "invalid_catalog_model_identifier"),
    ({"data": [{"id": "same"}, {"id": "same"}]}, "openai_duplicate_model_id"),
])
def test_invalid_openai_catalog_denied(obj, reason):
    with pytest.raises(module.CatalogHold, match=reason):
        module.compat_summary(obj)


def test_native_model_keys_are_not_echoed_in_report():
    secret = "/private/real-runtime/creds/internal-model-path"
    obj = {"models": [native_model(key=secret)]}
    out = module.native_summary(obj)
    assert secret not in json.dumps(out)


def test_auth_tokens_or_model_paths_are_never_in_output():
    secret = "/private/topology/model-a.gguf"
    obj = {"models": [native_model(key=secret)]}
    r, _ = run_fixture(native=obj)
    assert secret not in json.dumps(r)
    assert r["can_dispatch"] is False


class FakeResponse:
    def __init__(self, payload: bytes, *, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, length):
        return self.payload[:length]


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return self.response


def test_api_fetch_makes_only_bounded_get_requests_no_redirects():
    fake = FakeOpener(FakeResponse(b'{"models":[]}'))
    with patch.object(module, "build_opener", return_value=fake) as build:
        assert module.fetch_json(module.NATIVE_URL) == {"models": []}
    assert len(fake.calls) == 1
    req, timeout = fake.calls[0]
    assert req.get_method() == "GET"
    assert timeout == 4
    assert req.full_url == module.NATIVE_URL
    assert any(isinstance(h, module.NoRedirect) for h in build.call_args.args)
    assert any(
        isinstance(h, module.ProxyHandler) and not h.proxies
        for h in build.call_args.args
    )


def test_unauthorized_fetch_url_denied_before_network():
    with pytest.raises(module.CatalogHold, match="unapproved_catalog_url"):
        module.fetch_json("https://unreviewed.example/api/v1/models")


def test_redirect_cannot_follow():
    with pytest.raises(module.CatalogHold, match="redirected_model_catalog_forbidden"):
        module.NoRedirect().redirect_request(
            None, None, 302, "other", {}, "https://other.invalid/models"
        )


def test_fetch_rejects_non_200_without_leak():
    fake = FakeOpener(FakeResponse(b'{"models":[]}', status=202))
    with patch.object(module, "build_opener", return_value=fake):
        with pytest.raises(module.CatalogHold, match="catalog_http_not_200"):
            module.fetch_json(module.NATIVE_URL)


def test_fetch_rejects_oversized_response():
    fake = FakeOpener(FakeResponse(b"A" * (module.MAX_BYTES + 1)))
    with patch.object(module, "build_opener", return_value=fake):
        with pytest.raises(module.CatalogHold, match="catalog_size_invalid"):
            module.fetch_json(module.NATIVE_URL)


def test_fetch_duplicate_nested_json_key_denied():
    fake = FakeOpener(FakeResponse(
        b'{"models":[{"key":"a","key":"b","loaded_instances":[]}]}'
    ))
    with patch.object(module, "build_opener", return_value=fake):
        with pytest.raises(module.CatalogHold, match="duplicate_catalog_json_field"):
            module.fetch_json(module.NATIVE_URL)


def test_fetch_nonfinite_json_denied():
    fake = FakeOpener(FakeResponse(b'{"models":[NaN]}'))
    with patch.object(module, "build_opener", return_value=fake):
        with pytest.raises(module.CatalogHold, match="nonfinite_catalog_json"):
            module.fetch_json(module.NATIVE_URL)


def test_fetch_bad_unicode_denied():
    fake = FakeOpener(FakeResponse(b'\xff\xff'))
    with patch.object(module, "build_opener", return_value=fake):
        with pytest.raises(module.CatalogHold, match="catalog_json_invalid"):
            module.fetch_json(module.NATIVE_URL)


def test_missing_native_k2_family_is_absent_not_unavailable():
    r, _ = run_fixture(native={"models": []})
    assert r["all_catalogs_reachable"] is True
    assert r["native"]["available_k2_family_entries"] == 0
    assert r["status"] == "CATALOG_HEALTHY_ALL_MODELS_IDLE"
    assert r["can_dispatch"] is False


def test_cli_always_returns_nonzero_even_when_loaded_claims_agree(capsys):
    both = compat_fixture(k2=True)
    result, _ = run_fixture(
        native=native_fixture(k2_loaded=1),
        loopback=both, lan=both, tailscale=both,
    )
    with patch.object(module, "collect", return_value=result):
        assert module.main([]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["can_dispatch"] is False
    assert printed["status"] == "CATALOGS_INDICATE_K2_LOAD_NOT_PHYSICAL_PROOF"


def test_read_only_code_has_no_load_or_inference_call():
    code = SOURCE.read_text()
    for forbidden in (
        'Request(url, method="POST"', 'method="POST"',
        "models/load", "models/unload", "chat/completions",
        "subprocess.run(", "os.system(", "docker exec",
        "systemctl restart", "CREATE TABLE",
    ):
        assert forbidden not in code


def test_network_exception_is_never_returned_as_secret():
    def fetch(_url):
        raise urllib.error.URLError("bearer SECRET and private host address")
    r = module.collect(fetch=fetch)
    assert r["status"] == "CATALOG_UNAVAILABLE_HOLD"
    assert r["can_dispatch"] is False
    assert "SECRET" not in json.dumps(r)
