"""Read-only network latency collection for AssistX fleet candidates."""

from __future__ import annotations

import math
import statistics
import subprocess
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from .fleet_latency_shadow import (
    EndpointLatencyObservation,
    FleetLatencyMapV1,
    NetworkPathObservation,
    NodePressureObservation,
    ShadowAuthority,
)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate percentile of empty sample")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def parse_ping_output(output: str, *, transmitted: int | None = None) -> dict[str, float]:
    """Parse iputils ping output without treating summary averages as p95."""

    samples: list[float] = []
    packet_loss: float | None = None
    for line in output.splitlines():
        line = line.strip()
        marker = "time="
        if marker in line:
            fragment = line.split(marker, 1)[1].split()[0]
            try:
                samples.append(float(fragment))
            except ValueError:
                continue
        if "packet loss" in line and "%" in line:
            before = line.split("%", 1)[0]
            token = before.rsplit(",", 1)[-1].strip()
            try:
                packet_loss = float(token) / 100.0
            except ValueError:
                pass

    if not samples:
        raise ValueError("ping produced no latency samples")

    if packet_loss is None and transmitted:
        packet_loss = max(0.0, 1.0 - (len(samples) / transmitted))
    if packet_loss is None:
        packet_loss = 0.0

    deltas = [abs(right - left) for left, right in zip(samples, samples[1:])]
    return {
        "rtt_ms_p50": round(float(statistics.median(samples)), 3),
        "rtt_ms_p95": round(_percentile(samples, 0.95), 3),
        "jitter_ms_p95": round(_percentile(deltas, 0.95), 3) if deltas else 0.0,
        "loss_rate": round(min(max(packet_loss, 0.0), 1.0), 6),
        "samples": float(len(samples)),
    }


def _host_from_access_path(value: str) -> str:
    parsed = urlparse(value)
    if parsed.hostname:
        return parsed.hostname
    return value


def preferred_tailnet_targets(document: dict[str, Any]) -> list[dict[str, str]]:
    """Extract one preferred candidate access path per online Tailscale node.

    This preserves the discovery script's existing path priority.  The returned
    rows are reachability targets only and carry no admission semantics.
    """

    targets: list[dict[str, str]] = []
    for node in document.get("nodes") or []:
        if not isinstance(node, dict) or not bool(node.get("online", False)):
            continue
        paths = [
            path
            for path in node.get("candidate_access_paths") or []
            if isinstance(path, dict) and str(path.get("base_url") or "").strip()
        ]
        if not paths:
            continue
        path = min(paths, key=lambda row: int(row.get("priority") or 10_000))
        base_url = str(path["base_url"]).rstrip("/")
        targets.append(
            {
                "node_id": str(node.get("node_id") or ""),
                "host": _host_from_access_path(base_url),
                "transport": str(path.get("transport") or "unknown"),
                "access_path": base_url,
            }
        )
    return sorted(targets, key=lambda row: row["node_id"])


def collect_network_path(
    *,
    origin_node_id: str,
    target: dict[str, str],
    count: int = 4,
    timeout_seconds: int = 2,
    run: Any = subprocess.run,
    observed_at: datetime | None = None,
) -> NetworkPathObservation:
    """Collect a bounded ICMP observation for one candidate path."""

    host = str(target.get("host") or "").strip()
    if not host:
        raise ValueError("target host is required")
    completed = run(
        ["ping", "-c", str(max(1, count)), "-W", str(max(1, timeout_seconds)), host],
        check=False,
        capture_output=True,
        text=True,
    )
    metrics = parse_ping_output(
        completed.stdout or "",
        transmitted=max(1, count),
    )
    return NetworkPathObservation(
        origin_node_id=origin_node_id,
        target_node_id=str(target.get("node_id") or host),
        transport=str(target.get("transport") or "unknown"),
        access_path=target.get("access_path"),
        observed_at=observed_at or datetime.now(UTC),
        samples=int(metrics.pop("samples")),
        **metrics,
    )


def build_latency_map(
    *,
    origin_node_id: str,
    tailnet_candidates: dict[str, Any],
    endpoint_evidence: list[dict[str, Any]] | None = None,
    pressures: list[dict[str, Any]] | None = None,
    stale_after_seconds: int = 300,
    count: int = 4,
    timeout_seconds: int = 2,
    run: Any = subprocess.run,
    captured_at: datetime | None = None,
) -> tuple[FleetLatencyMapV1, list[dict[str, str]]]:
    """Build a map from candidate reachability plus pre-existing endpoint evidence.

    Endpoint evidence is validated but never generated here.  This collector
    therefore cannot turn discovery into model qualification.
    """

    now = captured_at or datetime.now(UTC)
    paths: list[NetworkPathObservation] = []
    failures: list[dict[str, str]] = []
    for target in preferred_tailnet_targets(tailnet_candidates):
        try:
            paths.append(
                collect_network_path(
                    origin_node_id=origin_node_id,
                    target=target,
                    count=count,
                    timeout_seconds=timeout_seconds,
                    run=run,
                    observed_at=now,
                )
            )
        except (OSError, ValueError) as exc:
            failures.append(
                {
                    "node_id": target["node_id"],
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )

    document = FleetLatencyMapV1(
        captured_at=now,
        origin_node_id=origin_node_id,
        stale_after_seconds=stale_after_seconds,
        network_paths=paths,
        endpoints=[
            EndpointLatencyObservation.model_validate(row)
            for row in (endpoint_evidence or [])
        ],
        pressures=[
            NodePressureObservation.model_validate(row)
            for row in (pressures or [])
        ],
        authority=ShadowAuthority(),
    )
    return document, failures
