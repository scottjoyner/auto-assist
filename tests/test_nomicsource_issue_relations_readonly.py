"""Offline, synthetic tests. No SSH, GitHub, inference, file writes or model loading."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/nomicsource_issue_relations_readonly.py"
spec = importlib.util.spec_from_file_location("nomicsource", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NONCE = "1234567890abcdef" * 2

ITEMS = [
    {"repo": "auto-assist", "number": 284,
     "title": "Canonical runtime projection expired and signed witnesses missing",
     "source_url": "https://github.com/scottjoyner/auto-assist/issues/284"},
    {"repo": "auto-router", "number": 52,
     "title": "Canonical runtime projection expired; reattestation required",
     "source_url": "https://github.com/scottjoyner/auto-router/issues/52"},
    {"repo": "sensorgraft", "number": 51,
     "title": "Research independent checkpoint custody and rollback gate",
     "source_url": "https://github.com/scottjoyner/sensorgraft/issues/51"},
]


def input_bytes(items=ITEMS):
    return m.canonical({"schema": m.INPUT_SCHEMA, "issues": items})


def remote_result(*, pairs=None, count=3, nonce=NONCE):
    return {"status": "EMBEDDINGS_COMPLETE_RESEARCH_ONLY",
            "model": m.MODEL, "nonce": nonce, "count": count,
            "dimensions": 768, "duration_seconds": 0.121,
            "pairs": [[0, 1, 0.823], [0, 2, 0.42], [1, 2, 0.31]]
            if pairs is None else pairs,
            "reported_usage": {"total_tokens": 0},
            "can_dispatch": False}


def parse_reply(obj=None, *, nonce=NONCE, count=3):
    return m.validate_remote(json.dumps(remote_result() if obj is None else obj),
                             nonce=nonce, count=count)


def test_normal_public_source_input_roundtrip():
    assert m.parse_input(input_bytes()) == ITEMS


@pytest.mark.parametrize(("data", "reason"), [
    (b"", "input_size_invalid"),
    (b"x" * 40000, "input_size_invalid"),
    (b"not-json", "input_json_invalid"),
    (b"[]", "input_schema_invalid"),
    (b'{"schema":"x","issues":[]}', "input_version_invalid"),
    (b'{"schema":"x","schema":"y","issues":[]}', "input_duplicate_json_field"),
    (b'{"schema":"assistx.open-issue-matches.input.v1","issues":[]}', "issue_count_invalid"),
])
def test_malformed_task_input_holds(data, reason):
    with pytest.raises(m.ResearchHold, match=reason):
        m.parse_input(data)


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("repo", "unknown", "issue_identity_invalid"),
    ("repo", ["auto-assist"], "issue_identity_invalid"),
    ("number", True, "issue_identity_invalid"),
    ("number", 0, "issue_identity_invalid"),
    ("number", 9999999, "issue_identity_invalid"),
    ("title", "bad", "issue_title_invalid"),
    ("title", "x" * 181, "issue_title_invalid"),
    ("title", "unsafe\nissue", "issue_title_control_characters"),
    ("source_url", "https://evil.example/track", "source_url_mismatch"),
    ("source_url", "http://github.com/scottjoyner/auto-assist/issues/284",
     "source_url_mismatch"),
    ("source_url", "https://github.com/scottjoyner/auto-assist/issues/52",
     "source_url_mismatch"),
])
def test_invalid_issue_entry_holds(field, value, reason):
    data = [dict(x) for x in ITEMS]
    data[0][field] = value
    with pytest.raises(m.ResearchHold, match=reason):
        m.parse_input(input_bytes(data))


def test_duplicate_issue_identity_rejected_even_with_new_title():
    entries = [*ITEMS, {**ITEMS[0], "title": "Updated issue title"}]
    with pytest.raises(m.ResearchHold, match="issue_duplicate"):
        m.parse_input(input_bytes(entries))


def test_extra_fields_cannot_smuggle_command():
    entries = [dict(x) for x in ITEMS]
    entries[0]["shell"] = "rm -rf /"
    with pytest.raises(m.ResearchHold, match="issue_record_schema_invalid"):
        m.parse_input(input_bytes(entries))


def test_valid_source_link_candidates_require_shared_subject():
    reply = parse_reply()
    links = m.rank_links(ITEMS, reply)
    assert len(links) == 1
    match = links[0]
    assert match["left"] == "auto-assist#284"
    assert match["right"] == "auto-router#52"
    assert "canonical" in match["common_subject_tokens"]
    assert "projection" in match["common_subject_tokens"]
    assert match["duplicate_claim_verified"] is False
    assert match["production_action_authorized"] is False
    assert match["review_status"] == "HUMAN_REVIEW_ONLY"


def test_similar_but_different_repo_topics_do_not_claim_duplicate():
    output = m.rank_links(ITEMS, parse_reply(remote_result(
        pairs=[[0, 2, 0.95], [0, 1, 0.55], [1, 2, 0.24]]
    )))
    assert not output


def test_same_repo_similar_titles_not_crossrepo_result():
    entries = [ITEMS[0], {**ITEMS[1], "repo": "auto-assist",
                         "source_url": "https://github.com/scottjoyner/auto-assist/issues/52"}]
    result = parse_reply(remote_result(count=2, pairs=[[0, 1, 0.99]]), count=2)
    assert m.rank_links(entries, result) == []


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("status", "APPROVED_PRODUCTION", "remote_status_untrusted"),
    ("model", "other", "remote_model_nonce_or_count_untrusted"),
    ("nonce", "wrong", "remote_model_nonce_or_count_untrusted"),
    ("count", True, "remote_model_nonce_or_count_untrusted"),
    ("dimensions", 0, "remote_dimensions_invalid"),
    ("dimensions", 4097, "remote_dimensions_invalid"),
    ("duration_seconds", -1, "duration_invalid"),
    ("pairs", [], "remote_pairs_invalid"),
    ("pairs", [[1, 0, .8]], "remote_pair_indices_invalid"),
    ("pairs", [[0, 3, .8]], "remote_pair_indices_invalid"),
    ("pairs", [[0, 1, 1.01]], "remote_similarity_invalid"),
    ("pairs", [[0, 1, .6], [0, 1, .5]], "remote_pair_order_invalid"),
    ("pairs", [[0, 1, .2], [0, 2, .8]], "remote_pair_order_invalid"),
])
def test_malicious_or_malformed_remote_claim_fails_closed(field, value, reason):
    obj = remote_result()
    obj[field] = value
    with pytest.raises(m.ResearchHold, match=reason):
        parse_reply(obj)


def test_remote_cannot_claim_production_dispatch():
    out = remote_result()
    out["can_dispatch"] = True
    with pytest.raises(m.ResearchHold, match="remote_authority_untrusted"):
        parse_reply(out)


def test_duplicate_json_fields_in_remote_reply_rejected():
    data = json.dumps(remote_result()).replace(
        '"can_dispatch": false', '"can_dispatch": false, "can_dispatch": false'
    )
    with pytest.raises(m.ResearchHold, match="remote_duplicate_json_field"):
        m.validate_remote(data, nonce=NONCE, count=3)


def test_remote_holds_do_not_echo_private_response():
    obj = remote_result()
    obj["status"] = "HOLD"
    obj["reason"] = "private token /process/cmdline"
    with pytest.raises(m.ResearchHold, match="remote_residency_resource_or_embedding_hold"):
        parse_reply(obj)


def test_fixed_strict_ssh_one_local_embedding_batch():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        req = json.loads(kwargs["input"])
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(
                remote_result(nonce=req["nonce"])
            ), stderr="SECRET_INTERNAL",)

    result = m.request_embeddings(ITEMS, runner=runner,
                                  nonce_factory=lambda n: bytes.fromhex(NONCE))
    assert len(result["pairs"]) == 3
    assert len(calls) == 1
    cmd, options = calls[0]
    assert cmd[0] == "ssh" and cmd[-2] == "xwing"
    assert options["timeout"] == 29 and options["check"] is False
    assert options["input"]
    for control in ("StrictHostKeyChecking=yes", "ForwardAgent=no",
                    "BatchMode=yes", "ClearAllForwardings=yes",
                    "ControlPath=none"):
        assert control in cmd
    assert "SECRET_INTERNAL" not in str(result)


def test_bad_ssh_exit_and_wrong_nonce_fail():
    with pytest.raises(m.ResearchHold, match="remote_probe_unavailable"):
        m.request_embeddings(
            ITEMS, nonce_factory=lambda n: bytes.fromhex(NONCE),
            runner=lambda *a, **k: SimpleNamespace(returncode=255, stdout=""),
        )
    with pytest.raises(m.ResearchHold, match="remote_model_nonce_or_count_untrusted"):
        m.request_embeddings(
            ITEMS, nonce_factory=lambda n: bytes.fromhex(NONCE),
            runner=lambda *a, **k: SimpleNamespace(
                returncode=0, stdout=json.dumps(remote_result(nonce="f" * 32))
            ),
        )


def test_embedded_usage_zero_is_not_reported_as_zero_token_truth():
    report = m.build_report(ITEMS, parse_reply())
    assert report["embedding_token_count_proven"] is False
    assert report["verified_embedding_tokens"] is None
    assert report["embedding_requests"] == 1
    assert report["review_only"] is True
    assert report["production_dispatch"] is False
    assert report["scheduled_research_authorized"] is False
    assert report["paid_api_calls"] == 0
    assert report["github_issue_mutations"] == 0
    assert report["production_graph_writes"] == 0
    assert report["input_sha256"] == __import__("hashlib").sha256(m.canonical(ITEMS)).hexdigest()


def test_default_cli_dry_run_never_ssh(monkeypatch, capsys):
    class Buffer:
        def read(self, _size):
            return input_bytes()
    monkeypatch.setattr(m.sys, "stdin", SimpleNamespace(buffer=Buffer()))
    with patch.object(m, "request_embeddings", side_effect=AssertionError("no SSH")):
        assert m.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "DRY_RUN"
    assert output["inference_requests"] == 0
    assert output["production_dispatch"] is False


def test_remote_source_never_loads_model_or_calls_paid_network():
    compile(m.REMOTE, "<fixed-nomic-remote>", "exec")
    for forbidden in (
        "models/load", "models/unload", "api/v1/chat",
        "chat/completions", "api/generate", "os.system(",
        "subprocess.run(", "shell=True", "systemctl restart",
        "https://", "assistx-api", "create_issue", "update_issue",
    ):
        assert forbidden not in m.REMOTE
    assert "http://127.0.0.1:1234/v1/embeddings" in m.REMOTE
    assert 'MODEL="text-embedding-nomic-embed-text-v1.5"' in m.REMOTE
