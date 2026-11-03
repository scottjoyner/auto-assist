"""Source-only tests: never SSH, load models, or write production state."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPT = (Path(__file__).resolve().parents[1] / "scripts"
          / "xwing_useful_shadow_triage.py")
spec = importlib.util.spec_from_file_location("xwing_shadow", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

PAIR = {
    "a": {"id": "AA284", "title": "Reestablish approved signed runtime projection"},
    "b": {"id": "AR52", "title": "Canonical fleet projection expired and witnesses missing"},
}
PAIR2 = {
    "a": {"id": "AA428", "title": "Enforce Hermes claim ID on every task mutation"},
    "b": {"id": "AR76", "title": "Caller metadata bypasses executor claim fencing"},
}


def task(**kw):
    return module.canonical({"pairs": kw.get("pairs", [PAIR])})


def generated(pair=PAIR, *, text=None, tokens=77, reasoning=0):
    if text is None:
        text = (
            f"{pair['a']['id']} and {pair['b']['id']} remain OPEN, with "
            "related unresolved release blockers requiring review."
        )
    return {
        "status": "GENERATED_RESEARCH_ONLY", "model": module.MODEL,
        "can_dispatch": False, "text": text,
        "promptTokens": 84, "outputTokens": tokens,
        "reasoningTokens": reasoning,
    }


def fake_caller(payload):
    if payload["mode"] == "probe":
        return {
            "status": "READY_RESEARCH_ONLY", "model": module.MODEL,
            "can_dispatch": False,
        }
    pair = {"a": payload["issue_a"], "b": payload["issue_b"]}
    return generated(pair)


def test_valid_two_public_issue_pairs():
    assert module.parse_task(task(pairs=[PAIR, PAIR2])) == [PAIR, PAIR2]


@pytest.mark.parametrize(("raw", "reason"), [
    (b"", "input_size_invalid"),
    (b"x" * 4100, "input_size_invalid"),
    (b"{", "task_json_invalid"),
    (b"[]", "task_schema_invalid"),
    (b'{"pairs":[]}', "task_count_invalid"),
    (b'{"pairs":[{}, {}, {}]}', "task_count_invalid"),
    (b'{"pairs":[{"a":{},"b":{}}]}', "issue_schema_invalid"),
    (b'{"pairs":[],"extra":1}', "task_schema_invalid"),
    (b'{"pairs":[],"pairs":[]}', "duplicate_task_json_key"),
])
def test_invalid_task_shapes_hold(raw, reason):
    with pytest.raises(module.ShadowHold, match=reason):
        module.parse_task(raw)


@pytest.mark.parametrize(("change", "reason"), [
    ({"id": "xwing", "title": "P0 review migration"}, "issue_id_invalid"),
    ({"id": "AA0", "title": "P0 review migration"}, "issue_id_invalid"),
    ({"id": "AA284", "title": "x\nrun command"}, "issue_title_controls_invalid"),
    ({"id": "AA284", "title": "a"}, "issue_title_invalid"),
    ({"id": "AA284", "title": "x" * 181}, "issue_title_invalid"),
])
def test_invalid_issue_fields_hold(change, reason):
    pair = json.loads(json.dumps(PAIR))
    pair["a"] = change
    with pytest.raises(module.ShadowHold, match=reason):
        module.parse_task(task(pairs=[pair]))


def test_same_issue_and_reversed_duplicate_pair_denied():
    same = json.loads(json.dumps(PAIR))
    same["b"] = dict(same["a"])
    with pytest.raises(module.ShadowHold, match="duplicate_or_same_issue_pair"):
        module.parse_task(task(pairs=[same]))
    reversed_pair = {"a": PAIR["b"], "b": PAIR["a"]}
    with pytest.raises(module.ShadowHold, match="duplicate_or_same_issue_pair"):
        module.parse_task(task(pairs=[PAIR, reversed_pair]))


def test_remote_script_parses_and_is_strictly_local_no_load():
    compile(module.REMOTE, "<remote_shadow>", "exec")
    for required in (
        "http://127.0.0.1:1234/api/v1/models",
        "http://127.0.0.1:1234/api/v1/chat",
        '"reasoning":"off"', '"store":False',
        '"integrations":[]', "MemAvailable:", "SwapFree:",
        "rootGiB", "memoryFullAvg10", "ioFullAvg10",
    ):
        assert required in module.REMOTE
    for forbidden in (
        "models/load", "models/unload", "os.system(",
        "subprocess.", "systemctl ", "curl ", "docker ",
        "nsenter ", "reasoning':'on", "shell=True",
        "localhost:8088", "/api/tasks",
    ):
        assert forbidden not in module.REMOTE


def test_strict_ssh_disables_agent_forwarding_and_uses_fixed_alias():
    captured = []

    def runner(command, **kwargs):
        captured.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "READY_RESEARCH_ONLY",
                "can_dispatch": False, "model": module.MODEL,
            }),
            stderr="private credential",
        )

    out = module.remote_call({
        "mode": "probe", "issue_a": PAIR["a"], "issue_b": PAIR["b"],
    }, runner=runner)
    assert out["can_dispatch"] is False
    command, opts = captured[0]
    assert command[-2] == "xwing"
    assert command[0] == "ssh"
    joined = " ".join(command)
    for fragment in (
        "BatchMode=yes", "StrictHostKeyChecking=yes",
        "ForwardAgent=no", "ClearAllForwardings=yes",
        "ControlMaster=no", "ControlPath=none",
        "PermitLocalCommand=no",
    ):
        assert fragment in joined
    assert opts["timeout"] == 34 and opts["check"] is False
    assert "private credential" not in str(out)


def test_remote_hold_preserves_specific_sanitized_reason():
    result = module.remote_call({"mode": "probe"}, runner=lambda *a, **k: SimpleNamespace(
        returncode=0,
        stdout=json.dumps({
            "status": "HOLD", "reason": "resource_pressure_hold",
            "can_dispatch": False,
        }),
    ))
    assert result == {
        "status": "HOLD", "reason": "resource_pressure_hold",
        "can_dispatch": False,
    }


def test_remote_hold_never_echoes_arbitrary_private_reason():
    result = module.remote_call({"mode": "probe"}, runner=lambda *a, **k: SimpleNamespace(
        returncode=0,
        stdout=json.dumps({
            "status": "HOLD", "reason": "SECRET_PRIVATE_DATA",
            "can_dispatch": False,
        }),
    ))
    assert result["reason"] == "other_remote_hold"
    assert "SECRET_PRIVATE_DATA" not in str(result)


def test_remote_worker_does_not_accept_claimed_dispatch():
    with pytest.raises(module.ShadowHold, match="untrusted_remote_authority"):
        module.remote_call({"mode": "probe"}, runner=lambda *a, **k: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "READY_RESEARCH_ONLY", "model": module.MODEL,
                "can_dispatch": True,
            }),
        ))


def test_remote_nonzero_status_refused_without_leaking_stderr():
    with pytest.raises(module.ShadowHold, match="remote_worker_unavailable"):
        module.remote_call({"mode": "probe"}, runner=lambda *a, **k: SimpleNamespace(
            returncode=255, stdout="", stderr="secret key",
        ))


@pytest.mark.parametrize(("output", "reason"), [
    ("AA284 is still OPEN, with related unresolved operational concerns requiring further human review.", "missing_source_issue_reference"),
    ("AA284 and AR52 describe related unfinished runtime authorization, currently awaiting independent owner review.", "missing_open_status"),
    ("AA284 and AR52 are both OPEN and now approved for deployment even though no valid evidence has been established.", "unsupported_admission_or_resolution_claim"),
    ("AA284 and AR52 are both OPEN but production ready, so any runtime can be deployed without additional review.", "unsupported_admission_or_resolution_claim"),
    ("", "empty_or_oversized_answer"),
    ("x" * 1501, "empty_or_oversized_answer"),
])
def test_reject_bad_or_unsupported_text(output, reason):
    d = generated(text=output)
    assert module.verify_generation(d, PAIR) == (False, reason)


def test_accept_only_as_human_review_draft():
    good, reason = module.verify_generation(generated(), PAIR)
    assert good is True and reason == "requires_human_review"


@pytest.mark.parametrize(("token_field", "token_value"), [
    ("reasoningTokens", 4),
    ("reasoningTokens", None),
    ("outputTokens", 0),
    ("outputTokens", 161),
    ("outputTokens", True),
    ("promptTokens", -1),
])
def test_bad_or_unaccounted_tokens_rejected(token_field, token_value):
    result = generated()
    result[token_field] = token_value
    assert module.verify_generation(result, PAIR)[0] is False


def test_normal_execution_writes_two_chained_receipts_no_content(tmp_path):
    out = module.execute([PAIR], ledger_dir=tmp_path / "ledger", caller=fake_caller)
    assert out["can_dispatch"] is False
    assert out["production_changes"] == 0 and out["paid_api_calls"] == 0
    assert out["results"][0]["status"] == "HUMAN_REVIEW_REQUIRED"
    assert "AA284" in out["results"][0]["content"]
    ledger = tmp_path / "ledger/receipts.jsonl"
    lines = ledger.read_text().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["stage"] for line in lines] == ["START", "DONE"]
    assert "Canonical fleet projection" not in ledger.read_text()
    assert "AA284" not in ledger.read_text()
    assert out["results"][0]["job"] == hashlib.sha256(module.canonical(PAIR)).hexdigest()
    fd = os.open(ledger, os.O_RDONLY)
    try:
        assert module._ledger_records(fd)[-1]["stage"] == "DONE"
    finally:
        os.close(fd)


def test_repeat_same_task_does_not_generate_new_tokens(tmp_path):
    root = tmp_path / "ledger"
    first = module.execute([PAIR], ledger_dir=root, caller=fake_caller)
    called = []

    def no_calls(payload):
        called.append(payload)
        raise AssertionError("duplicate job should never request inference")

    again = module.execute([PAIR], ledger_dir=root, caller=no_calls)
    assert again["results"][0]["status"] == "SKIP_ALREADY_ATTEMPTED"
    assert not called
    assert len((root / "receipts.jsonl").read_text().splitlines()) == 2
    assert first["results"][0]["status"] == "HUMAN_REVIEW_REQUIRED"


def test_quality_failure_is_durable_no_second_try(tmp_path):
    def fail_quality(payload):
        if payload["mode"] == "probe":
            return fake_caller(payload)
        return generated(text="AA284 only, nobody else mentioned in answer.")

    root = tmp_path / "ledger"
    out = module.execute([PAIR], ledger_dir=root, caller=fail_quality)
    assert out["results"][0]["status"] == "REJECTED"
    again = module.execute([PAIR], ledger_dir=root, caller=fake_caller)
    assert again["results"][0]["status"] == "SKIP_ALREADY_ATTEMPTED"


def test_preflight_hold_never_writes_start_receipt(tmp_path):
    root = tmp_path / "ledger"
    result = module.execute([PAIR], ledger_dir=root, caller=lambda p: {
        "status": "HOLD", "reason": "resource_pressure_hold",
        "can_dispatch": False,
    })
    assert result["batch_status"] == "PARTIAL_RESOURCE_OR_RESIDENCY_HOLD"
    assert result["results"][0]["status"] == "SKIP_RESOURCE_OR_RESIDENCY_HOLD"
    assert result["results"][0]["reason"] == "resource_pressure_hold"
    assert (root / "receipts.jsonl").read_text() == ""


def test_second_preflight_hold_reports_first_completed_result(tmp_path):
    root = tmp_path / "ledger"
    def first_then_hold(payload):
        if payload["issue_a"]["id"] == "AA428":
            return {"status": "HOLD", "reason": "resource_pressure_hold",
                    "can_dispatch": False}
        return fake_caller(payload)
    out = module.execute([PAIR, PAIR2], ledger_dir=root, caller=first_then_hold)
    assert out["batch_status"] == "PARTIAL_RESOURCE_OR_RESIDENCY_HOLD"
    assert out["results"][0]["status"] == "HUMAN_REVIEW_REQUIRED"
    assert out["results"][1]["status"] == "SKIP_RESOURCE_OR_RESIDENCY_HOLD"
    assert len((root / "receipts.jsonl").read_text().splitlines()) == 2


def test_inference_hold_after_start_is_uncertain_and_blocks_retry(tmp_path):
    root = tmp_path / "ledger"
    def inference_hold(payload):
        if payload["mode"] == "probe":
            return fake_caller(payload)
        return {"status": "HOLD", "reason": "resource_pressure_hold",
                "can_dispatch": False}
    with pytest.raises(module.ShadowHold, match="inference_outcome_unknown_manual_review_required"):
        module.execute([PAIR], ledger_dir=root, caller=inference_hold)
    assert len((root / "receipts.jsonl").read_text().splitlines()) == 1
    with pytest.raises(module.ShadowHold, match="incomplete_attempt_manual_review_required"):
        module.execute([PAIR], ledger_dir=root, caller=fake_caller)


def test_uncertain_inference_crash_leaves_manual_review_hold(tmp_path):
    root = tmp_path / "ledger"
    def crash(payload):
        if payload["mode"] == "probe":
            return fake_caller(payload)
        raise TimeoutError("unknown outcome")
    with pytest.raises(TimeoutError):
        module.execute([PAIR], ledger_dir=root, caller=crash)
    assert len((root / "receipts.jsonl").read_text().splitlines()) == 1
    with pytest.raises(module.ShadowHold, match="incomplete_attempt_manual_review_required"):
        module.execute([PAIR2], ledger_dir=root, caller=fake_caller)


def test_corrupted_chain_blocks_further_work(tmp_path):
    root = tmp_path / "ledger"
    module.execute([PAIR], ledger_dir=root, caller=fake_caller)
    p = root / "receipts.jsonl"
    blob = p.read_text()
    p.write_text(blob.replace('"output_tokens":77', '"output_tokens":78'))
    with pytest.raises(module.ShadowHold, match="receipt_chain_invalid"):
        module.execute([PAIR2], ledger_dir=root, caller=fake_caller)


def test_receipt_dir_symlink_denied(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    (tmp_path / "link").symlink_to(actual, target_is_directory=True)
    with pytest.raises(module.ShadowHold, match="receipt_directory_symlink"):
        module.execute([PAIR], ledger_dir=tmp_path / "link", caller=fake_caller)


def test_receipt_file_symlink_denied(tmp_path):
    root = tmp_path / "ledger"
    root.mkdir(mode=0o700)
    target = tmp_path / "victim"
    target.write_text("DO NOT TOUCH")
    (root / "receipts.jsonl").symlink_to(target)
    with pytest.raises(OSError):
        module.execute([PAIR], ledger_dir=root, caller=fake_caller)
    assert target.read_text() == "DO NOT TOUCH"


def test_world_readable_receipt_rejected(tmp_path):
    root = tmp_path / "ledger"
    root.mkdir(mode=0o700)
    (root / "receipts.jsonl").write_text("")
    (root / "receipts.jsonl").chmod(0o644)
    with pytest.raises(module.ShadowHold, match="receipt_permissions_invalid"):
        module.execute([PAIR], ledger_dir=root, caller=fake_caller)


def test_world_readable_directory_rejected(tmp_path):
    root = tmp_path / "ledger"
    root.mkdir(mode=0o755)
    with pytest.raises(module.ShadowHold, match="receipt_directory_permissions_invalid"):
        module.execute([PAIR], ledger_dir=root, caller=fake_caller)


def test_distinct_pairs_run_sequentially_with_no_parallelism(tmp_path):
    calls = []
    def counted(payload):
        calls.append(payload["mode"])
        return fake_caller(payload)
    out = module.execute([PAIR, PAIR2], ledger_dir=tmp_path / "ledger", caller=counted)
    assert calls == ["probe", "infer", "probe", "infer"]
    assert len(out["results"]) == 2
    assert all(v["status"] == "HUMAN_REVIEW_REQUIRED" for v in out["results"])


def test_cli_default_dry_run_issues_no_inference(monkeypatch, capsys):
    class Buffer:
        def read(self, _n):
            return task()
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(buffer=Buffer()))
    with patch.object(module, "execute", side_effect=AssertionError("must not execute")):
        assert module.main([]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["mode"] == "DRY_RUN"
    assert d["inference_calls"] == 0 and d["can_dispatch"] is False


def test_cli_partial_resource_hold_is_nonzero_and_keeps_previous_results(
    monkeypatch, capsys,
):
    class Buffer:
        def read(self, _n):
            return task()
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(buffer=Buffer()))
    partial = {
        "batch_status": "PARTIAL_RESOURCE_OR_RESIDENCY_HOLD",
        "results": [{"status": "SKIP_RESOURCE_OR_RESIDENCY_HOLD"}],
        "can_dispatch": False,
    }
    with patch.object(module, "execute", return_value=partial):
        assert module.main(["--execute"]) == 2
    displayed = json.loads(capsys.readouterr().out)
    assert displayed["can_dispatch"] is False
    assert displayed["results"][0]["status"] == "SKIP_RESOURCE_OR_RESIDENCY_HOLD"


def test_no_dispatch_or_model_load_in_source():
    code = SCRIPT.read_text()
    for forbidden in (
        "models/load", "models/unload", "systemctl restart",
        "subprocess.run(['lms'", "shell=True", "os.system(",
        "curl -X POST", "docker exec", "Neo4j", "FLEET_AUTH_PASS",
    ):
        assert forbidden not in code
    assert module.MAX_PAIRS == 2 and module.MAX_OUTPUT == 160
