"""Static escaped HTML for historical 24-hour rolling burn-rate snapshots."""
from __future__ import annotations

import html
from burn_history_projection import rolling_gauge_trend


def recent_gauge_panel(snapshot, history):
    points = list(history or [])
    epoch = snapshot.get("updated_epoch")
    if snapshot.get("complete") is True and type(epoch) in (int, float) and epoch > 0:
        values = {
            p["provider"]: p["observed_tokens_24h"]
            for p in snapshot.get("providers", [])
            if isinstance(p.get("provider"), str)
            and type(p.get("observed_tokens_24h")) is int
        }
        if values:
            current = {"epoch": epoch, "providers": values, "total": sum(values.values())}
            points = [p for p in points if p.get("epoch") != epoch] + [current]
    points.sort(key=lambda r: r["epoch"])
    trend = rolling_gauge_trend(points)
    if trend["status"] == "observed":
        delta = trend["change"]
        label = ("+" if delta > 0 else "") + f"{delta:,}"
        message = f"Rolling 24h gauge movement: {label} across {trend['sample_count']} observations over {trend['span_minutes']} minutes"
    else:
        message = "Insufficient history: two observations separated by at least five minutes required"
    values = trend["gauges"]
    highest = max(values, default=0)
    heights = [max(2, round(35 * n / highest)) if highest else 2 for n in values]
    bars = " ".join(chr(9601 + min(7, h // 5)) for h in heights)
    return (
        "<section><strong>Rolling 24h consumption history</strong><p>"
        + html.escape(message, quote=True) + "</p>"
        + "<pre>" + bars + "</pre>"
        + "<small>Each block is an independently sampled trailing-24h total, not "
        "new 15-minute consumption. Do not sum these observations.</small></section>"
    )
