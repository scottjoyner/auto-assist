#!/usr/bin/env python3
"""Read-only observed burn and conditional forecasts for fleet model endpoints.

No inference, provider calls, credential loading, routing, or lease mutation.
OpenCode step-finish timestamps support event-time rates; session lifetime
tokens are deliberately NOT assigned to session_updated timestamps.
"""
from __future__ import annotations
import argparse
import html
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import pwd
import sqlite3
import time

HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
DEFAULT_DBS = (
    HOME / ".local/share/opencode/opencode.db",
    HOME / ".local/share/mcp/dc-isolated/.home/.local/share/opencode/opencode.db",
)
DEFAULT_PRESENCE = HOME / ".local/state/auto-assist/provider-presence.json"
KEYS = ("input", "output", "reasoning", "cache_read", "cache_write", "total")
WINDOWS = {"1h": 3600, "24h": 86400, "7d": 604800}
MAX_SESSIONS = 5000
MAX_PARTS_PER_SESSION = 250000


def empty_tokens():
    return {key: 0 for key in KEYS}


def parse_step(raw):
    try:
        event = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(event, dict) or event.get("type") != "step-finish":
        return None
    value = event.get("tokens")
    if not isinstance(value, dict):
        return None
    cache = value.get("cache") or {}
    if not isinstance(cache, dict):
        cache = {}
    fields = {"input": value.get("input"), "output": value.get("output"),
              "reasoning": value.get("reasoning"),
              "cache_read": cache.get("read"), "cache_write": cache.get("write")}
    if any(type(v) is not int or v < 0 for v in fields.values() if v is not None):
        return None
    result = {k: int(v or 0) for k, v in fields.items()}
    total = value.get("total")
    result["total"] = total if type(total) is int and total >= 0 else sum(result.values())
    return result


def load_events(databases=DEFAULT_DBS, *, now=None, days=7):
    now = time.time() if now is None else float(now)
    cutoff = int((now - days * 86400) * 1000)
    seen = set()
    events = []
    coverage = []
    for path in map(Path, databases):
        info = {"source": str(path), "available": path.is_file(), "complete": False,
                "sessions": 0, "step_events": 0, "duplicates": 0, "error": None}
        coverage.append(info)
        if not path.is_file():
            info["error"] = "missing_database"
            continue
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
            db.execute("PRAGMA query_only=ON")
            sessions = db.execute(
                "SELECT id,model FROM session WHERE time_updated >= ? "
                "ORDER BY time_updated DESC LIMIT ?", (cutoff, MAX_SESSIONS + 1)
            ).fetchall()
            info["complete"] = len(sessions) <= MAX_SESSIONS
            for sid, model_text in sessions[:MAX_SESSIONS]:
                if sid in seen:
                    info["duplicates"] += 1
                    continue
                seen.add(sid)
                info["sessions"] += 1
                try:
                    model = json.loads(model_text or "{}")
                    if not isinstance(model, dict):
                        model = {}
                except (ValueError, TypeError):
                    model = {}
                provider = str(model.get("providerID") or "unknown")
                model_id = str(model.get("id") or model.get("modelID") or "unknown")
                parts = db.execute(
                    "SELECT time_created,data FROM part WHERE session_id=? "
                    "AND time_created >= ? ORDER BY time_created LIMIT ?",
                    (sid, cutoff, MAX_PARTS_PER_SESSION + 1),
                ).fetchall()
                if len(parts) > MAX_PARTS_PER_SESSION:
                    info["complete"] = False
                for stamp, raw in parts[:MAX_PARTS_PER_SESSION]:
                    tokens = parse_step(raw)
                    if tokens is None or stamp > now * 1000 + 60000:
                        continue
                    events.append((provider, model_id, stamp / 1000, tokens))
                    info["step_events"] += 1
            db.close()
        except (OSError, sqlite3.Error, ValueError) as exc:
            info["complete"] = False
            info["error"] = type(exc).__name__
    return events, coverage


def read_presence(path=DEFAULT_PRESENCE):
    try:
        source = json.loads(Path(path).read_text(encoding="utf-8"))
        return {key: {"status": value.get("status", "unknown"),
                       "checked_epoch": value.get("checked_epoch"),
                       "inference_qualified": bool(value.get("inference_qualified", False))}
                for key, value in (source.get("providers") or {}).items()
                if isinstance(value, dict)}
    except (OSError, ValueError, TypeError):
        return {}


def load_policy(path):
    if path is None:
        return {}
    policy = json.loads(Path(path).read_text(encoding="utf-8"))
    if policy.get("schema") != "free-burn-policy/v1" or not isinstance(policy.get("providers"), dict):
        raise ValueError("invalid free-burn-policy/v1")
    return policy["providers"]


def build_report(events, coverage, presence=None, policy=None, *, now=None):
    now = time.time() if now is None else float(now)
    presence = presence or {}
    policy = policy or {}
    groups = defaultdict(list)
    for provider, model, stamp, tokens in events:
        groups[(provider, model)].append((stamp, tokens))
    rows = []
    for (provider, model), values in groups.items():
        windows = {}
        for name, seconds in WINDOWS.items():
            total = empty_tokens()
            for stamp, tokens in values:
                if now - seconds <= stamp <= now:
                    for key in KEYS:
                        total[key] += tokens[key]
            windows[name] = total
        latest = max(stamp for stamp, _ in values)
        planned = policy.get(provider, {}).get("planned_tokens_per_day")
        if planned is not None and (type(planned) is not int or planned <= 0):
            raise ValueError("planned_tokens_per_day must be positive integer")
        observed_daily_pace = windows["24h"]["total"]
        hourly = [0] * 24
        for stamp, tokens in values:
            idx = int((stamp - (now - 86400)) // 3600)
            if 0 <= idx < 24:
                hourly[idx] += tokens["total"]
        rows.append({
            "provider": provider, "model": model, "upstream_quota_group": None,
            "name_suggests_free": (":free" in model.lower()
                                   or model.lower().endswith("-free")),
            "eligibility": "UNVERIFIED", "quota_remaining": None,
            "events": len(values), "last_event_epoch": latest,
            "age_minutes": round((now - latest) / 60, 1),
            "windows": windows, "hourly_24h": hourly,
            "observed_tokens_per_hour_24h": round(observed_daily_pace / 24, 1),
            "observed_tokens_per_hour_7d": round(windows["7d"]["total"] / 168, 1),
            "forecast_next_24h_flat_pace": observed_daily_pace,
            "forecast_next_7d_flat_pace": observed_daily_pace * 7,
            "planned_tokens_per_day": planned,
            "pace_vs_plan": (round(observed_daily_pace / planned, 3)
                             if planned else None),
        })
    rows.sort(key=lambda row: (-row["windows"]["7d"]["total"],
                               row["provider"], row["model"]))
    by_provider = {}
    for row in rows:
        name = row["provider"]
        group = by_provider.setdefault(name, {"provider": name, "models": 0,
            "observed_tokens_1h": 0, "observed_tokens_24h": 0,
            "observed_tokens_7d": 0, "forecast_next_24h": 0,
            "token_classes_24h": empty_tokens(),
            "planned_tokens_per_day": policy.get(name, {}).get("planned_tokens_per_day"),
            "upstream_quota_group": None, "provider_quota_remaining": None})
        group["models"] += 1
        for window in WINDOWS:
            group["observed_tokens_" + window] += row["windows"][window]["total"]
        group["forecast_next_24h"] += row["forecast_next_24h_flat_pace"]
        for token_class in KEYS:
            group["token_classes_24h"][token_class] += row["windows"]["24h"][token_class]
    for name in set(by_provider) | set(presence):
        group = by_provider.setdefault(name, {"provider": name, "models": 0,
            "observed_tokens_1h": None, "observed_tokens_24h": None,
            "observed_tokens_7d": None, "forecast_next_24h": None,
            "planned_tokens_per_day": policy.get(name, {}).get("planned_tokens_per_day"),
            "upstream_quota_group": None, "provider_quota_remaining": None})
        group["presence"] = presence.get(name, {}).get("status", "unobserved")
        group["inference_qualified"] = False
    return {
        "schema": "free-endpoint-burn-dashboard/v1",
        "generated_epoch": now, "read_only": True,
        "external_inference_requests": 0,
        "aggregation": "timestamped step-finish observations (no session-lifetime snapshots)",
        "forecast_method": "last 24h tokens repeated for next 24h; heuristic, not quota",
        "complete": bool(coverage) and all(x["complete"] and not x["error"] for x in coverage),
        "coverage": coverage, "sessions": sum(x["sessions"] for x in coverage),
        "step_events": len(events),
        "free_named_observed_24h": sum(r["windows"]["24h"]["total"]
                                       for r in rows if r["name_suggests_free"]),
        "unverified_name_observed_24h": sum(r["windows"]["24h"]["total"]
                                          for r in rows if not r["name_suggests_free"]),
        "by_provider": sorted(by_provider.values(), key=lambda x: x["provider"]),
        "models": rows,
    }


def fmt(value):
    if value is None:
        return "—"
    return f"{value:,.0f}"


def sparkline(values):
    peak = max(values, default=0)
    if not peak:
        return '<span class="quiet">No timed events in the last 24h</span>'
    bars = []
    for n in values:
        height = max(1, round(35 * n / peak)) if n else 0
        bars.append(f'<i style="height:{height}px" title="{n:,} tokens"></i>')
    return '<div class="spark" aria-label="24 hourly token volumes">' + "".join(bars) + "</div>"


def render_html(report):
    esc = lambda value: html.escape(str(value), quote=True)
    providers = []
    for row in report["by_provider"]:
        plan = row["planned_tokens_per_day"]
        observed = row["observed_tokens_24h"]
        utilization = f"{observed / plan:.0%}" if plan and observed is not None else "—"
        providers.append("<tr>" + "".join("<td>" + esc(v) + "</td>" for v in (
            row["provider"], row["presence"], fmt(row["observed_tokens_1h"]),
            fmt(observed), fmt(row["observed_tokens_7d"]),
            fmt((row.get("token_classes_24h") or {}).get("cache_read")),
            fmt(row["forecast_next_24h"]), fmt(plan), utilization
        )) + "</tr>")
    models = []
    for row in report["models"]:
        models.append("<tr>" +
            "<td>" + esc(row["provider"]) + "</td>" +
            "<td>" + esc(row["model"]) + "</td>" +
            "<td>" + ("name only" if row["name_suggests_free"] else "unverified") + "</td>" +
            "<td>" + fmt(row["windows"]["24h"]["total"]) + "</td>" +
            "<td>" + fmt(row["windows"]["7d"]["total"]) + "</td>" +
            "<td>" + esc(row["age_minutes"]) + " min</td>" +
            "<td>" + sparkline(row["hourly_24h"]) + "</td></tr>")
    total24 = sum(row["observed_tokens_24h"] or 0 for row in report["by_provider"])
    total7 = sum(row["observed_tokens_7d"] or 0 for row in report["by_provider"])
    generated = datetime.fromtimestamp(report["generated_epoch"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = "COMPLETE" if report["complete"] else "PARTIAL — source missing/truncated"
    header = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Fleet free-endpoint burn</title>
<style>
:root {font:14px system-ui,sans-serif;color:#e2e8f0;background:#0b1220}
body{max-width:1440px;margin:auto;padding:24px} h1{margin-bottom:4px}
small,.sub,.quiet{color:#94a3b8} .alert{border-left:3px solid #d97706;padding:10px 14px;background:#1d2130}
.metrics{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}
.metric{background:#172338;border:1px solid #334155;border-radius:9px;padding:16px;min-width:170px}
.metric strong{font-size:23px;display:block} .metric span{color:#94a3b8}
table{border-collapse:collapse;width:100%;margin:12px 0 27px}
td,th{border-bottom:1px solid #334155;padding:10px 9px;text-align:left;vertical-align:middle}
th{font-size:12px;color:#94a3b8} tr:hover{background:#172338}
.tablewrap{overflow:auto} .spark{height:36px;display:flex;align-items:end;gap:2px;min-width:125px}
.spark i{width:5px;display:inline-block;background:#60a5fa;border-radius:2px 2px 0 0}
details{margin-top:14px}.foot{border-top:1px solid #334155;padding-top:14px;margin-top:25px}
</style></head><body>"""
    metric = lambda label, value: '<div class="metric"><strong>' + esc(value) + '</strong><span>' + esc(label) + '</span></div>'
    page = header + "<h1>Fleet model endpoint burn</h1>"
    page += '<p class="sub">Generated ' + esc(generated) + " · " + esc(status) + " · observation only</p>"
    page += '<p class="alert">No provider generation or quota requests were issued. Zero reported cost is not a verified entitlement. The forward estimates assume the last 24 hours repeat; quota remaining and physical upstream grouping are unknown.</p>'
    page += '<div class="metrics">' + metric("All tracked tokens, last 24h", fmt(total24))
    page += metric("Free-named models, last 24h", fmt(report["free_named_observed_24h"]))
    page += metric("All tracked tokens, last 7d", fmt(total7))
    page += metric("Timestamped model steps", fmt(report["step_events"]))
    page += metric("Forecast next 24h (flat pace)", fmt(total24)) + '</div>'
    page += '<h2>Provider usage and planning</h2><div class="tablewrap"><table><thead><tr>'
    page += "<th>Provider</th><th>Catalog health</th><th>Last 1h</th><th>Last 24h</th>"
    page += "<th>Last 7d</th><th>Cache reads 24h</th><th>Next 24h estimate</th><th>Operator plan/day</th><th>Observed ÷ plan</th>"
    page += "</tr></thead><tbody>" + "".join(providers) + "</tbody></table></div>"
    page += "<h2>Model detail</h2><div class='tablewrap'><table><thead><tr>"
    page += "<th>Provider</th><th>Requested model</th><th>Free hint (not verified)</th><th>Last 24h</th><th>Last 7d</th>"
    page += "<th>Last event age</th><th>Trailing 24h hourly activity</th></tr></thead><tbody>"
    page += "".join(models) + "</tbody></table></div>"
    page += "<details><summary>Read-only accounting and limitations</summary><p>"
    page += "Provider/model identities are taken from OpenCode sessions; aliases may share one physical upstream quota. "
    page += "Deduplicated session IDs across local and isolated databases. Per-step token totals include cache and reasoning where reported. "
    page += "A configured operator plan is not a vendor quota. Prices, account billing and residual allowance have not been checked. "
    page += "Missing telemetry, deferred writes and sessions outside these databases are not counted.</p></details>"
    page += '<p class="foot sub">No inference qualification, lease admission, routing or execution permission is derived from this dashboard.</p>'
    return page + "</body></html>\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only free-provider token burn view")
    parser.add_argument("--db", action="append", type=Path, dest="databases")
    parser.add_argument("--presence", type=Path, default=DEFAULT_PRESENCE)
    parser.add_argument("--policy", type=Path, help="Optional operator planning targets, never vendor quotas")
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-html", type=Path)
    args = parser.parse_args(argv)
    events, coverage = load_events(args.databases or DEFAULT_DBS)
    report = build_report(events, coverage, read_presence(args.presence), load_policy(args.policy))
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        os.chmod(args.out_json, 0o600)
    if args.out_html:
        args.out_html.parent.mkdir(parents=True, exist_ok=True)
        args.out_html.write_text(render_html(report), encoding="utf-8")
        os.chmod(args.out_html, 0o600)
    if not args.out_json and not args.out_html:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(json.dumps({"complete": report["complete"], "steps": report["step_events"],
                          "providers": len(report["by_provider"]),
                          "generated": [str(args.out_json) if args.out_json else None,
                                        str(args.out_html) if args.out_html else None]}))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
