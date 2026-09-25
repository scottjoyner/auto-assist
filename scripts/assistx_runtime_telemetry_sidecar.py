#!/usr/bin/env python3
"""Read-only runtime telemetry bridge for AssistX inference experiments.

This reference sidecar exposes one GET endpoint and performs no runtime
mutation. It combines:
- immutable runtime identity supplied by the operator;
- llama.cpp Prometheus metrics when configured;
- Linux AMD GPU gauges from sysfs when configured;
- a hash of the observed runtime process command line.

It is intentionally small so it can run next to a benchmark-only runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SCHEMA = "assistx-runtime-telemetry-snapshot-v1"

_LLAMA_COUNTERS = {
    "llamacpp:prompt_tokens_total": "prefill_tokens",
    "llamacpp:prompt_seconds_total": "prefill_seconds",
    "llamacpp:prompt_tokens_cached_total": "cache_reused_tokens",
    "llamacpp:tokens_predicted_total": "decode_tokens",
    "llamacpp:tokens_predicted_seconds_total": "decode_seconds",
    "llamacpp:spec_decode_num_draft_tokens_total": "spec_proposed_tokens",
    "llamacpp:spec_decode_num_accepted_tokens_total": "spec_accepted_tokens",
    "llamacpp:spec_decode_num_drafts_total": "spec_verification_steps",
}

_LLAMA_GAUGES = {
    "llamacpp:prompt_tokens_seconds": "prefill_tokens_per_second",
    "llamacpp:predicted_tokens_seconds": "decode_tokens_per_second",
    "llamacpp:requests_processing": "requests_processing",
    "llamacpp:requests_deferred": "requests_deferred",
    "llamacpp:n_busy_slots_per_decode": "busy_slots_per_decode",
}


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _runtime_pid() -> int:
    value = int(_required_env("ASSISTX_TELEMETRY_RUNTIME_PID"))
    if value <= 0:
        raise RuntimeError("ASSISTX_TELEMETRY_RUNTIME_PID must be positive")
    return value


def _process_started_at_unix_ms(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    parts = stat.split()
    start_ticks = int(parts[21])
    ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])

    boot_seconds = None
    for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
        if line.startswith("btime "):
            boot_seconds = int(line.split()[1])
            break
    if boot_seconds is None:
        raise RuntimeError("cannot determine system boot time")

    return int((boot_seconds + (start_ticks / ticks)) * 1000)


def _launch_config_sha256(pid: int) -> str:
    raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    if not raw:
        raise RuntimeError("runtime process cmdline is empty")
    return hashlib.sha256(raw).hexdigest()


def _parse_prometheus(text: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pieces = line.split()
        if len(pieces) < 2:
            continue
        name = pieces[0].split("{", 1)[0]
        try:
            value = float(pieces[-1])
        except ValueError:
            continue
        metrics[name] = value
    return metrics


def _fetch_llama_metrics() -> tuple[dict[str, float], dict[str, Any]]:
    url = os.getenv("ASSISTX_TELEMETRY_LLAMA_METRICS_URL", "").strip()
    if not url:
        return {}, {"configured": False}

    model = os.getenv("ASSISTX_TELEMETRY_LLAMA_METRICS_MODEL", "").strip()
    if model:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}model={model}"

    request = urllib.request.Request(
        url,
        headers={"Accept": "text/plain"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            body = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return {}, {
            "configured": True,
            "ok": False,
            "error": str(exc)[:300],
        }
    return _parse_prometheus(body), {
        "configured": True,
        "ok": True,
        "url_sha256": hashlib.sha256(url.encode()).hexdigest(),
    }


def _read_number(path: Path) -> float | None:
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _gpu_sysfs() -> tuple[dict[str, float], dict[str, Any]]:
    root_raw = os.getenv("ASSISTX_TELEMETRY_DRM_DEVICE_SYSFS", "").strip()
    if not root_raw:
        return {}, {"configured": False}
    root = Path(root_raw)

    gauges: dict[str, float] = {}
    vram = _read_number(root / "mem_info_vram_used")
    if vram is not None:
        gauges["vram_bytes"] = vram

    busy = _read_number(root / "gpu_busy_percent")
    if busy is not None:
        gauges["gpu_utilization_percent"] = busy

    hwmon_root = root / "hwmon"
    if hwmon_root.exists():
        for candidate in sorted(hwmon_root.glob("hwmon*/power1_average")):
            microwatts = _read_number(candidate)
            if microwatts is not None:
                gauges["power_watts"] = microwatts / 1_000_000.0
                break

    return gauges, {
        "configured": True,
        "root": str(root),
    }


def snapshot(trial_id: str | None) -> dict[str, Any]:
    pid = _runtime_pid()
    llama, llama_source = _fetch_llama_metrics()
    gpu, gpu_source = _gpu_sysfs()

    counters: dict[str, float] = {}
    for source_name, target_name in _LLAMA_COUNTERS.items():
        if source_name in llama:
            counters[target_name] = llama[source_name]

    gauges = dict(gpu)
    for source_name, target_name in _LLAMA_GAUGES.items():
        if source_name in llama:
            gauges[target_name] = llama[source_name]

    # /metrics is process cumulative. requests_total is deliberately omitted
    # unless an upstream adapter provides it; the joiner therefore does not
    # assert single-request isolation from a fabricated counter.
    return {
        "schema": SCHEMA,
        "scope": "process",
        "trial_id": trial_id,
        "observed_at_unix_ms": int(time.time() * 1000),
        "identity": {
            "node_id": _required_env("ASSISTX_TELEMETRY_NODE_ID"),
            "model_handle": _required_env(
                "ASSISTX_TELEMETRY_MODEL_HANDLE"
            ),
            "backend": _required_env("ASSISTX_TELEMETRY_BACKEND"),
            "quantization": _required_env(
                "ASSISTX_TELEMETRY_QUANTIZATION"
            ),
            "speculation": _required_env(
                "ASSISTX_TELEMETRY_SPECULATION"
            ),
            "runtime_revision": _required_env(
                "ASSISTX_TELEMETRY_RUNTIME_REVISION"
            ),
            "launch_config_sha256": _launch_config_sha256(pid),
            "process_started_at_unix_ms": _process_started_at_unix_ms(pid),
        },
        "counters": counters,
        "gauges": gauges,
        "source": {
            "llama_metrics": llama_source,
            "gpu_sysfs": gpu_source,
            "runtime_pid": pid,
            "read_only": True,
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AssistXRuntimeTelemetry/1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._json(200, {"ok": True, "read_only": True})
            return
        if parsed.path != "/v1/telemetry/snapshot":
            self._json(404, {"error": "not_found"})
            return

        query = parse_qs(parsed.query)
        trial_id = (
            query.get("trial_id", [None])[0]
            if query
            else None
        )
        try:
            value = snapshot(trial_id)
        except Exception as exc:
            self._json(
                503,
                {
                    "error": "snapshot_failed",
                    "detail": str(exc)[:500],
                },
            )
            return
        self._json(200, value)

    def do_POST(self) -> None:
        self._json(405, {"error": "read_only"})

    def do_PUT(self) -> None:
        self._json(405, {"error": "read_only"})

    def do_DELETE(self) -> None:
        self._json(405, {"error": "read_only"})

    def log_message(self, format: str, *args: Any) -> None:
        if os.getenv("ASSISTX_TELEMETRY_ACCESS_LOG", "false").lower() in {
            "1",
            "true",
            "yes",
        }:
            super().log_message(format, *args)

    def _json(self, status: int, value: Any) -> None:
        body = json.dumps(
            value,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = os.getenv("ASSISTX_TELEMETRY_BIND", "127.0.0.1")
    port = int(os.getenv("ASSISTX_TELEMETRY_PORT", "9188"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(
        json.dumps(
            {
                "listening": f"http://{host}:{port}",
                "read_only": True,
                "schema": SCHEMA,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
