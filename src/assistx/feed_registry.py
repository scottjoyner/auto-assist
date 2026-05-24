from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class FeedConnector:
    id: str
    name: str
    category: str
    enabled: bool
    health_status: str
    endpoint: str


def _parse_registry_env(raw: str) -> List[FeedConnector]:
    # format: id|name|category|enabled|health|endpoint ; ...
    out: List[FeedConnector] = []
    for idx, item in enumerate([x.strip() for x in raw.split(";") if x.strip()]):
        parts = [p.strip() for p in item.split("|")]
        if len(parts) < 6:
            continue
        enabled = parts[3].lower() in {"1", "true", "yes", "on"}
        health = parts[4].lower()
        if health not in {"healthy", "degraded", "down"}:
            health = "degraded"
        out.append(
            FeedConnector(
                id=parts[0] or f"feed-{idx}",
                name=parts[1] or f"feed-{idx}",
                category=parts[2] or "general",
                enabled=enabled,
                health_status=health,
                endpoint=parts[5],
            )
        )
    return out


def get_feed_connectors() -> List[FeedConnector]:
    raw = os.getenv("ASSISTX_FEED_CONNECTORS", "").strip()
    if raw:
        parsed = _parse_registry_env(raw)
        if parsed:
            return parsed
    # sensible defaults for Phase 9 skeleton
    return [
        FeedConnector("market-prices", "Market Prices", "market", True, "healthy", "local://market-prices"),
        FeedConnector("macro-indicators", "Macro Indicators", "macro", True, "healthy", "local://macro-indicators"),
        FeedConnector("earnings-calendar", "Earnings Calendar", "events", False, "degraded", "local://earnings-calendar"),
        FeedConnector("sophia-voice-auth", "Sophia Voice Auth Feed", "voice", True, "healthy", "sophia://voice/auth"),
        FeedConnector("sophia-meeting-transcript", "Sophia Meeting Transcript Feed", "voice", True, "healthy", "sophia://voice/meeting_transcript"),
        FeedConnector("sophia-speaker-timeline", "Sophia Speaker Timeline Feed", "voice", True, "healthy", "sophia://voice/speaker_timeline"),
        FeedConnector("sophia-voice-policy", "Sophia Voice Policy Feed", "voice", True, "healthy", "sophia://voice/policy"),
    ]


def feed_health_summary() -> Dict[str, object]:
    feeds = get_feed_connectors()
    by_status: Dict[str, int] = {"healthy": 0, "degraded": 0, "down": 0}
    enabled = 0
    for f in feeds:
        by_status[f.health_status] = by_status.get(f.health_status, 0) + 1
        if f.enabled:
            enabled += 1
    return {
        "total": len(feeds),
        "enabled": enabled,
        "by_status": by_status,
        "connectors": [
            {
                "id": f.id,
                "name": f.name,
                "category": f.category,
                "enabled": f.enabled,
                "health_status": f.health_status,
                "endpoint": f.endpoint,
                "updated_at_ts": int(time.time() * 1000),
            }
            for f in feeds
        ],
    }
