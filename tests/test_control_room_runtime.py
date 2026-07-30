from __future__ import annotations

from assistx import control_room_runtime


def test_collect_router_telemetry_enriches_task_and_runtime(monkeypatch) -> None:
    calls = []

    def fake_query(_factory, query, parameters):
        calls.append((query, parameters))
        if "MATCH (e:EventEnvelope)" in query:
            return [
                {
                    "event_id": "event-1",
                    "event_type": "router.execution_stage.completed",
                    "correlation_id": "corr-1",
                    "envelope_node_id": "xwing",
                    "created_at_ts": 1_000,
                    "payload_json": {
                        "request_id": "request-1",
                        "task_id": "task-1",
                        "status": "completed",
                        "agent": "hermes-local",
                        "model_key": "local/qwen",
                        "model": "qwen.gguf",
                        "runtime_node_id": "xwing",
                        "runtime_instance_id": "lmstudio-xwing-1234",
                        "runtime_kind": "lmstudio",
                        "runtime_version": "0.4.7",
                        "selected_transport": "lan",
                        "selected_access_url": "http://192.168.1.9:1234/v1",
                        "quantization": "Q4_K_M",
                        "context_length": 32768,
                        "stage": "final",
                        "queue_wait_ms": 12,
                        "load_time_ms": 210,
                        "tokens_per_second": 8.5,
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "started_at_ms": 1_000,
                        "ended_at_ms": 3_000,
                        "latency_ms": 2_000,
                    },
                }
            ]
        return [
            {
                "task_id": "task-1",
                "title": "Backtest BTC breakout strategy",
                "task_kind": "analysis",
                "repository": "portfolio-management",
                "payload_json": "{}",
            }
        ]

    monkeypatch.setattr(control_room_runtime, "_query", fake_query)
    telemetry = control_room_runtime.collect_router_telemetry(lambda: None)

    assert telemetry["event_count"] == 1
    event = telemetry["activity"][0]
    assert event["display_title"] == "Backtest BTC breakout strategy"
    assert event["repository"] == "portfolio-management"
    assert event["runtime_instance_id"] == "lmstudio-xwing-1234"
    assert event["selected_transport"] == "lan"
    assert event["tokens_per_second"] == 8.5
    assert event["ttft_ms"] == 210
    assert telemetry["performance"][0]["tps_avg"] == 8.5
    assert telemetry["runtime_samples"][0]["quantization"] == "Q4_K_M"
    assert len(calls) == 2


def test_install_control_room_runtime_shares_cached_snapshot(monkeypatch) -> None:
    control_room_runtime._CACHE_VALUE = None
    control_room_runtime._CACHE_EXPIRES_AT = 0.0
    counts = {"base": 0, "telemetry": 0}

    class Module:
        @staticmethod
        def build_overview(_factory):
            counts["base"] += 1
            return {
                "summary": {},
                "runtimes": [],
                "activity": [],
                "performance": [],
            }

    def fake_telemetry(_factory):
        counts["telemetry"] += 1
        return {"activity": [], "performance": [], "runtime_samples": [], "event_count": 0}

    monkeypatch.setenv("ASSISTX_CONTROL_ROOM_CACHE_SECONDS", "60")
    monkeypatch.setattr(control_room_runtime, "collect_router_telemetry", fake_telemetry)
    control_room_runtime.install_control_room_runtime(Module)

    first = Module.build_overview(lambda: None)
    second = Module.build_overview(lambda: None)

    assert first == second
    assert first is not second
    assert counts == {"base": 1, "telemetry": 1}
