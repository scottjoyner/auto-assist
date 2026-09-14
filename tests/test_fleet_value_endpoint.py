"""Endpoint-granular value layer: dual-inference nodes serve per-variant
endpoints (optiplex:1234 reasoner vs optiplex:1235 executor), so benchmark
capability rows must be distinguishable per endpoint while host-level rows
from legacy run artifacts keep working."""

import csv

from assistx import fleet


def _write_runs(tmp_path, capability_rows):
    run = tmp_path / "run-20260913"
    run.mkdir(parents=True)
    cap = run / "capability_matrix.csv"
    with cap.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "host_name", "endpoint", "model_key", "task_family",
                "grade", "score", "recommended_use", "avoid_use",
            ],
        )
        writer.writeheader()
        for row in capability_rows:
            writer.writerow(row)
    return tmp_path


def _load(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "LMS_RUNS_DIR", str(tmp_path))
    fleet._load_value_data(force=True)


def test_endpoint_rows_are_distinct_from_host_rows(tmp_path, monkeypatch):
    rows = [
        # Same host and model, two different variants on different ports.
        {"host_name": "optiplex", "endpoint": "1235", "model_key": "minicpm5-2b-iter5",
         "task_family": "tool_use", "grade": "a", "score": "1.0", "avoid_use": ""},
        {"host_name": "optiplex", "endpoint": "1234", "model_key": "minicpm5-2b-iter5",
         "task_family": "tool_use", "grade": "f", "score": "0.1", "avoid_use": "tool_use"},
        # Host-level row (legacy artifact without endpoint data).
        {"host_name": "optiplex", "endpoint": "", "model_key": "minicpm5-2b-iter5",
         "task_family": "reasoning", "grade": "b", "score": "0.7", "avoid_use": ""},
    ]
    _load(_write_runs(tmp_path, rows), monkeypatch)

    assert ("optiplex", "1235", "minicpm5-2b-iter5", "tool_use") in fleet._value_index
    assert ("optiplex", "1234", "minicpm5-2b-iter5", "tool_use") in fleet._value_index
    assert ("optiplex", "", "minicpm5-2b-iter5", "reasoning") in fleet._value_index


def test_value_cap_prefers_exact_endpoint_then_host_row(tmp_path, monkeypatch):
    rows = [
        {"host_name": "optiplex", "endpoint": "1235", "model_key": "cpm",
         "task_family": "tool_use", "grade": "a", "score": "1.0", "avoid_use": ""},
        {"host_name": "optiplex", "endpoint": "", "model_key": "cpm",
         "task_family": "tool_use", "grade": "c", "score": "0.5", "avoid_use": ""},
    ]
    _load(_write_runs(tmp_path, rows), monkeypatch)

    exact = fleet._value_cap("optiplex", "cpm", "tool_use", endpoint="1235")
    assert exact["grade"] == "a"
    # Unknown endpoint falls back to the host-level row.
    fallback = fleet._value_cap("optiplex", "cpm", "tool_use", endpoint="9999")
    assert fallback["grade"] == "c"
    # No endpoint at all: host-level row.
    assert fleet._value_cap("optiplex", "cpm", "tool_use")["grade"] == "c"


def test_host_port_in_host_field_is_split(tmp_path, monkeypatch):
    rows = [
        {"host_name": "optiplex:1235", "endpoint": "", "model_key": "cpm",
         "task_family": "tool_use", "grade": "a", "score": "0.9", "avoid_use": ""},
    ]
    _load(_write_runs(tmp_path, rows), monkeypatch)

    cap = fleet._value_cap("optiplex", "cpm", "tool_use", endpoint="1235")
    assert cap is not None and cap["grade"] == "a"


def test_endpoint_parsed_from_lms_base_url_column(tmp_path, monkeypatch):
    # lms capability_matrix.csv rows carry base_url instead of an endpoint
    # column; the port distinguishes dual-inference variants.
    run = tmp_path / "run-lms"
    run.mkdir(parents=True)
    with (run / "capability_matrix.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["run_id", "host_name", "host_ip", "base_url", "model_key",
                        "task_family", "score", "grade", "recommended_use", "avoid_use"],
        )
        writer.writeheader()
        writer.writerow({
            "run_id": "1787719575", "host_name": "optiplex", "host_ip": "192.168.1.139",
            "base_url": "http://100.69.158.114:1235/v1", "model_key": "minicpm5-2b-iter5",
            "task_family": "tool_use", "score": "1.0000", "grade": "A",
            "recommended_use": "preferred for this task family", "avoid_use": "",
        })
        writer.writerow({
            "run_id": "1787719575", "host_name": "optiplex", "host_ip": "192.168.1.139",
            "base_url": "http://100.69.158.114:1234/v1", "model_key": "vibethinker-3b",
            "task_family": "tool_use", "score": "0.1000", "grade": "F",
            "recommended_use": "", "avoid_use": "avoid for tool tasks",
        })
        writer.writerow({
            "run_id": "1787719575", "host_name": "optiplex", "host_ip": "192.168.1.139",
            "base_url": "http://100.69.158.114/v1", "model_key": "edge-model",
            "task_family": "summarization", "score": "0.8000", "grade": "B",
            "recommended_use": "", "avoid_use": "",
        })
    _load(tmp_path, monkeypatch)

    assert ("optiplex", "1235", "minicpm5-2b-iter5", "tool_use") in fleet._value_index
    assert ("optiplex", "1234", "vibethinker-3b", "tool_use") in fleet._value_index
    # base_url without an explicit port stays host-level.
    assert ("optiplex", "", "edge-model", "summarization") in fleet._value_index


def test_port_of_base_url():
    assert fleet._port_of_base_url("http://100.69.158.114:1235/v1") == "1235"
    assert fleet._port_of_base_url("http://192.168.1.139/v1") == ""
    assert fleet._port_of_base_url("100.69.158.114:1234") == "1234"
    assert fleet._port_of_base_url("") == ""
    assert fleet._port_of_base_url("http://host:notaport/v1") == ""


def test_value_factor_uses_endpoint_row(tmp_path, monkeypatch):
    rows = [
        {"host_name": "optiplex", "endpoint": "1235", "model_key": "cpm",
         "task_family": "tool_use", "grade": "a", "score": "1.0", "avoid_use": ""},
        {"host_name": "optiplex", "endpoint": "1234", "model_key": "cpm",
         "task_family": "tool_use", "grade": "f", "score": "0.1",
         "avoid_use": "tool_use"},
        {"host_name": "optiplex", "endpoint": "", "model_key": "cpm",
         "task_family": "tool_use", "grade": "c", "score": "0.5", "avoid_use": ""},
    ]
    _load(_write_runs(tmp_path, rows), monkeypatch)
    full_id = "lmstudio-optiplex.cpm"

    # Executor variant: strong score -> rewarded.
    assert fleet._value_factor(full_id, "cpm", "tool_use", endpoint="1235") > 1.0
    # Reasoner variant: avoid_use for the family -> crushed.
    assert fleet._value_factor(full_id, "cpm", "tool_use", endpoint="1234") <= 0.15
    # Endpoint unknown: host-level row -> score 0.5 maps to 0.875.
    assert fleet._value_factor(full_id, "cpm", "tool_use") == 0.875
    # No endpoint hint at all: same host-level path, unchanged legacy behavior.
    assert fleet._value_factor(full_id, "cpm", "tool_use", endpoint="") == 0.875


def test_legacy_csv_without_endpoint_column(tmp_path, monkeypatch):
    run = tmp_path / "run-old"
    run.mkdir(parents=True)
    with (run / "capability_matrix.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["host_name", "model_key", "task_family", "grade", "score"],
        )
        writer.writeheader()
        writer.writerow({"host_name": "destroyer", "model_key": "lfm25",
                         "task_family": "summarization", "grade": "b", "score": "0.8"})
    _load(tmp_path, monkeypatch)

    assert ("destroyer", "", "lfm25", "summarization") in fleet._value_index
    assert fleet._value_cap("destroyer", "lfm25", "summarization")["grade"] == "b"
