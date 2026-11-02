"""Run-model canary unit tests: all providers mocked, no real GPU/model work."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "scripts/x1-k2-synthetic-canary.py"
spec = importlib.util.spec_from_file_location("k2_canary", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _preflight(*, catalog_count=0, sha=True, version="0.4.28+2"):
    return {
        "installed_version": version,
        "model_sha256_match": sha,
        "cli_matches_installed_app": True,
        "memory_guard_pass": True,
        "root_space_guard_pass": True,
        "api": {"available": True, "loaded_models": catalog_count},
        "estimate": {"status": "estimate_succeeded", "estimated_total_gib": 5.13},
    }


class _Pre:
    def __init__(self, report):
        self.report = report

    def observe(self, *, with_estimate):
        assert with_estimate
        return self.report

    def root_free_bytes(self):
        return 25 * 1024**3

    def available_memory_bytes(self):
        return 64 * 1024**3


def _setup(tmp_path, pre=None, *, catalog=None, cli=None, completion=None):
    pre = pre or _preflight()
    return patch.dict(module.run_canary.__globals__, {
        "LOCK": tmp_path / "canary.lock",
        "load_preflight": lambda: _Pre(pre),
        "check_catalog": catalog or (lambda: []),
        "_cli": cli or (lambda *a, **kw: SimpleNamespace(returncode=0)),
        "_completion": completion or (lambda: {
            "usage_provider_reported": True, "response_is_nonempty": True,
            "prompt_tokens": 13, "completion_tokens": 2, "total_tokens": 15}),
    })


def test_success_decodes_and_unloads_own_model(tmp_path):
    states = iter([[], [module.MODEL_ID], []])
    calls = []

    def cli(args, *, timeout):
        calls.append((args, timeout))
        return SimpleNamespace(returncode=0)

    with _setup(tmp_path, catalog=lambda: next(states), cli=cli):
        result = module.run_canary()
    assert result["result"] == "CANARY_COMPLETED_MEASURED"
    assert result["cleanup"]["confirmed_absent"] is True
    assert calls[0][0] == [
        "load", module.MODEL_ID, "--identifier", module.MODEL_ID,
        "--context-length", "64000", "--parallel", "4", "--ttl", "90", "--yes"]
    assert calls[1][0] == ["unload", module.MODEL_ID]
    assert result["production_router_requests"] == 0
    assert result["authorizes_dispatch"] is False


def test_failed_load_still_unloads_own_identifier(tmp_path):
    calls = []

    def cli(args, *, timeout):
        calls.append(args)
        return SimpleNamespace(returncode=1 if args[0] == "load" else 0)

    with _setup(tmp_path, cli=cli):
        result = module.run_canary()
    assert result["reason"] == "load_failed"
    assert result["cleanup"]["confirmed_absent"]
    assert calls[-1] == ["unload", module.MODEL_ID]
    assert result["result"] == "HOLD"


def test_existing_models_fail_closed_without_unload(tmp_path):
    def cli(*args, **kwargs):
        raise AssertionError("no CLI should be invoked")

    with _setup(tmp_path, pre=_preflight(catalog_count=1), cli=cli):
        result = module.run_canary()
    assert result["reason"] == "preflight_blocked"
    assert not result["cleanup"]["unload_attempted"]


def test_unverified_unload_fails_closed(tmp_path):
    states = iter([[], [module.MODEL_ID], [module.MODEL_ID]])
    with _setup(tmp_path, catalog=lambda: next(states)):
        result = module.run_canary()
    assert result["result"] == "HOLD_CLEANUP_UNVERIFIED"
    assert not result["cleanup"]["confirmed_absent"]


def test_decode_error_still_unloads(tmp_path):
    states = iter([[], [module.MODEL_ID], []])
    def fails():
        raise TimeoutError("synthetic failure")
    with _setup(tmp_path, catalog=lambda: next(states), completion=fails):
        result = module.run_canary()
    assert result["result"] == "HOLD"
    assert result["reason"] == "canary_exception_TimeoutError"
    assert result["cleanup"]["confirmed_absent"]


def test_unreviewed_version_and_hash_never_attempt_load(tmp_path):
    for bad in (_preflight(version="0.4.29"), _preflight(sha=False)):
        with _setup(tmp_path, pre=bad, cli=lambda *a, **k: 1):
            result = module.run_canary()
        assert result["result"] == "HOLD"
        assert result["reason"] == "preflight_blocked"
        assert not result["cleanup"]["unload_attempted"]


def test_completion_includes_only_hashed_synthetic_content():
    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return None
        def read(self, n):
            return (b'{"choices":[{"message":{"content":"OK"}}],"usage":'
                    b'{"prompt_tokens":21,"completion_tokens":2,"total_tokens":23}}')
    class Opener:
        def open(self, req, timeout):
            assert req.full_url == module.API + "/chat/completions"
            assert b"synthetic nonproduction canary" in req.data
            return Resp()
    with patch.object(module.urllib.request, "build_opener", return_value=Opener()):
        evidence = module._completion()
    assert evidence["usage_provider_reported"]
    assert evidence["completion_tokens"] == 2
    assert evidence["response_sha256"]
    assert "OK" not in str(evidence.values())
    assert "secret" not in str(evidence)


def test_reasoning_only_length_hit_is_hold_without_leaking_thoughts():
    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self,*args):return None
        def read(self, n):
            return (b'{"choices":[{"message":{"content":"","reasoning_content":"internal synthetic"}'
                    b',"finish_reason":"length"}],"usage":'
                    b'{"prompt_tokens":27,"completion_tokens":192,"total_tokens":219}}')
    class Opener:
        def open(self, req, timeout):return Resp()
    with patch.object(module.urllib.request, "build_opener", return_value=Opener()):
        out = module._completion()
    assert not out["response_is_nonempty"]
    assert out["reasoning_content_field_present"]
    assert out["finish_reason"] == "length"
    assert out["completion_tokens"] == 192
    assert "internal synthetic" not in repr(out)
