#!/usr/bin/env python3
"""Atomically render a safe, static provider burn panel from a precomputed report.

This is for the existing authenticated AssistX /traces template. It requires
no application restart, provider call, network request, database write, or
change to provider admission. Rendered values are allowlisted by the projection.
"""
from __future__ import annotations

import argparse
import html
import os
import pathlib
import tempfile
from datetime import datetime, timezone

from assistx.provider_burn_routes import project_burn_report


def fmt_tokens(value):
    if value is None:
        return "—"
    if value >= 1_000_000_000:
        return f"{value/1_000_000_000:,.2f}B"
    if value >= 1_000_000:
        return f"{value/1_000_000:,.2f}M"
    if value >= 1000:
        return f"{value/1000:,.1f}K"
    return f"{value:,}"


def escape(value):
    return html.escape(str(value), quote=True)


def render(snapshot):
    state = snapshot.get("state", "unavailable")
    complete = snapshot.get("complete") is True
    updated = snapshot.get("updated_epoch")
    stamped = (datetime.fromtimestamp(updated, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
               if type(updated) in (int, float) and updated > 0 else "Unavailable")
    header = (
        '<section id="provider-burn" class="panel" style="margin:16px 0">'
        '<h2>Provider usage &amp; burn rate · observational</h2>'
        '<p>Collected: ' + escape(stamped) + ' · ' + escape(state.upper())
        + (' · COMPLETE LOCAL SOURCES' if complete else ' · PARTIAL / UNKNOWN COVERAGE')
        + '</p><p>No inference or quota requests from this dashboard. '
        'All model entitlements, provider quotas and reset times are unverified. '
        'Model tokens include reported cache reads and reasoning; not billable units.</p>'
    )
    if state == "unavailable":
        return header + '<p>Snapshot unavailable; no zero-usage assumption.</p></section>\n'
    total = fmt_tokens(snapshot.get("observed_tokens_24h")) if complete else "—"
    forecast = fmt_tokens(snapshot.get("forecast_next_24h")) if complete else "—"
    body = '<p><strong>Observed 24h: ' + total + '</strong> &nbsp;·&nbsp; '
    body += '<strong>Next 24h flat-pace estimate: ' + forecast + '</strong>'
    body += '&nbsp;·&nbsp; Remaining free allowance: <strong>Unknown</strong></p>'
    rows = sorted(snapshot.get("providers", []), key=lambda r: (
        -(r.get("observed_tokens_24h") or 0), r.get("provider", "")))
    body += '<div style="overflow:auto"><table style="width:100%;border-collapse:collapse">'
    body += '<thead><tr><th>Provider</th><th>Catalog status</th><th>1h</th>'
    body += '<th>24h</th><th>7d</th><th>Cache 24h</th><th>Next 24h</th></tr></thead><tbody>'
    for row in rows[:15]:
        fields = (
            row.get("provider"), row.get("presence"),
            fmt_tokens(row.get("observed_tokens_1h")),
            fmt_tokens(row.get("observed_tokens_24h")),
            fmt_tokens(row.get("observed_tokens_7d")),
            fmt_tokens(row.get("cache_read_24h")),
            fmt_tokens(row.get("forecast_next_24h")),
        )
        body += "<tr>" + "".join('<td style="padding:5px;border-bottom:1px solid #445">' +
                                 escape(v if v is not None else "—") + "</td>"
                                 for v in fields) + "</tr>"
    body += "</tbody></table></div>"
    body += '<p>OpenCode account + isolated session databases only. No direct APIs, '
    body += 'cross-node aggregation, confirmed entitlement, or physical upstream quota mapping. '
    body += 'Historical and stalled traces remain available below.</p>'
    return header + body + '</section>\n'


def atomic_private_write(path, content):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".provider-burn-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def main(argv=None):
    p = argparse.ArgumentParser(description="Render a read-only provider panel")
    p.add_argument("--report", required=True, type=pathlib.Path)
    p.add_argument("--out", required=True, type=pathlib.Path)
    args = p.parse_args(argv)
    content = render(project_burn_report(args.report))
    atomic_private_write(args.out, content)
    print("rendered: " + str(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
