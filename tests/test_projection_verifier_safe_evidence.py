"""Synthetic projection evidence: no untrusted Router fields in report or console."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/verify-runtime-projection.py"
SENTINEL = "SYNTHETIC_UNTRUSTED_SECRET_MUST_NOT_PERSIST"
SHA = "a" * 64


def verifier_module():
    spec = importlib.util.spec_from_file_location("projection_evidence_allowlist", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_documents():
    assistx = {
        "source": "assistx", "generation": 7, "revision": "release-rev",
        "checksum": SHA, "expires_at_ms": 1800000000000,
        "providers": [{"name": SENTINEL}],
    }
    router = {
        "configured": True, "last_error": None,
        "current": {
            "generation": 7, "revision": "release-rev", "checksum": SHA,
            "admission": [{"runtime_instance_id": SENTINEL, "parallel_slots": 2,
                           "active": 0, "queued": 0, "secret": SENTINEL}],
            "access_paths": [{"url": "http://" + SENTINEL}],
            "providers": [SENTINEL], "unexpected": {"nested": SENTINEL},
        },
        "retired_generations": [{"generation": 6, "revision": SENTINEL,
                                  "checksum": "b" * 64, "secret": SENTINEL}],
        "unexpected": SENTINEL,
    }
    return assistx, router


def run_fixture(monkeypatch, capsys, tmp_path, assistx, router):
    module = verifier_module()
    calls = []

    def synthetic_get(url, headers):
        calls.append((url, tuple(headers)))
        return assistx if len(calls) == 1 else router

    monkeypatch.setattr(module, "_get_json", synthetic_get)
    monkeypatch.setenv("BASIC_AUTH_USER", "synthetic-user")
    monkeypatch.setenv("BASIC_AUTH_PASS", "synthetic-only")
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-only")
    outfile = tmp_path / "projection-report.json"
    monkeypatch.setattr(sys, "argv", ["verify-runtime-projection.py",
                      "--assistx-url", "http://test.invalid",
                      "--router-url", "http://router.invalid",
                      "--output", str(outfile)])
    rc = module.main()
    captured = capsys.readouterr()
    assert outfile.exists()
    raw = outfile.read_text()
    assert SENTINEL not in raw + captured.out + captured.err
    document = json.loads(raw)
    checksum = document.pop("evidence_sha256")
    assert len(checksum) == 64
    assert checksum == module._sha256_json(document)
    return rc, document, captured


def test_allowlisted_success_keeps_digest_not_raw_router_payload(monkeypatch, capsys, tmp_path):
    assistx, router = sample_documents()
    rc, doc, output = run_fixture(monkeypatch, capsys, tmp_path, assistx, router)
    assert rc == 0 and doc["status"] == "pass"
    assert doc["router_projection"]["generation"] == 7
    assert doc["router_projection"]["checksum"] == SHA
    assert doc["assistx_projection"]["generation"] == 7
    assert "revision_sha256" in doc["router_projection"]
    assert "revision_sha256" in doc["assistx_projection"]
    assert "revision" not in doc["router_projection"]
    assert "access_paths" not in doc["router_projection"]
    assert len(doc["retired_generations"]) == 1
    assert "RUNTIME_PROJECTION_GATE: PASS" in output.out


def test_reported_remote_error_and_invalid_slot_deny_safely(monkeypatch, capsys, tmp_path):
    assistx, router = sample_documents()
    router["last_error"] = SENTINEL
    router["current"]["admission"][0]["parallel_slots"] = SENTINEL
    rc, doc, output = run_fixture(monkeypatch, capsys, tmp_path, assistx, router)
    assert rc == 1 and doc["status"] == "blocked"
    assert "RUNTIME_PROJECTION_GATE: BLOCKED" in output.err
    assert doc["failures"]
    assert all(SENTINEL not in x for x in doc["failures"])


def test_untrusted_identity_types_deny_instead_of_echo_or_crash(monkeypatch, capsys, tmp_path):
    assistx, router = sample_documents()
    assistx["generation"] = SENTINEL
    router["current"]["checksum"] = SENTINEL
    router["retired_generations"] = [SENTINEL]
    rc, doc, _ = run_fixture(monkeypatch, capsys, tmp_path, assistx, router)
    assert rc == 1 and doc["status"] == "blocked"
    assert doc["failures"]
    assert doc["router_projection"]["checksum"] is None

def test_unpaired_unicode_revision_is_blocked_without_traceback(monkeypatch, capsys, tmp_path):
    assistx, router = sample_documents()
    assistx["revision"] = "\ud800"
    rc, doc, _ = run_fixture(monkeypatch, capsys, tmp_path, assistx, router)
    assert rc == 1 and doc["status"] == "blocked"
    assert doc["assistx_projection"]["revision_sha256"] is None


def test_excessive_retired_and_admission_counts_are_bounded(monkeypatch, capsys, tmp_path):
    assistx, router = sample_documents()
    router["retired_generations"] = [
        {"generation": i + 1, "revision": "rev", "checksum": SHA}
        for i in range(25)
    ]
    router["current"]["admission"] = [
        {"parallel_slots": 2} for _ in range(1030)
    ]
    rc, doc, _ = run_fixture(monkeypatch, capsys, tmp_path, assistx, router)
    assert rc == 1 and doc["status"] == "blocked"
    assert len(doc["retired_generations"]) <= 16
    assert len(doc["router_projection"]["admission"]) <= 1024


def test_true_boolean_or_string_generation_is_not_accepted(monkeypatch, capsys, tmp_path):
    assistx, router = sample_documents()
    assistx["generation"] = True
    router["current"]["generation"] = "7"
    rc, doc, _ = run_fixture(monkeypatch, capsys, tmp_path, assistx, router)
    assert rc == 1 and doc["status"] == "blocked"
    assert doc["assistx_projection"]["generation"] is None
    assert doc["router_projection"]["generation"] is None
