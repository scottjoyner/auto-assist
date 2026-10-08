"""Escaped node custody summary for existing authenticated AssistX traces."""
from __future__ import annotations
import html
from datetime import datetime, timezone


def render(report):
    escape = lambda x: html.escape(str(x), quote=True)
    stamp = datetime.fromtimestamp(report["generated_epoch"], timezone.utc)
    title = '<section id="provider-fleet-custody" class="panel">'
    title += '<h2>Fleet OpenCode archive custody</h2>'
    title += "<p>Observed " + escape(stamp.strftime("%Y-%m-%d %H:%M UTC")) + "</p>"
    title += ("<p>Manifest coverage: " + str(report["observed_manifests"]) + "/"
              + str(report["expected_nodes"]) + "; quarantined "
              + str(report["quarantined_nodes"]) + "; stale "
              + str(report["stale_nodes"]) + "; archive hashes verified "
              + str(report["content_hash_verified_nodes"]) + "</p>")
    title += "<p>Archive manifests are claims, not proof of distinct token usage. "
    title += "Cross-node token aggregation: NOT QUALIFIED.</p>"
    title += '<table><thead><tr><th>Node</th><th>Status</th>'
    title += '<th>Age</th><th>SHA checked</th><th>Issue</th></tr></thead><tbody>'
    for entry in report["nodes"]:
        cells = (
            entry["node"], entry["status"],
            ("—" if entry.get("age_hours") is None else str(entry["age_hours"]) + "h"),
            "yes" if entry["archive_hash_verified"] else "not in this run",
            entry.get("reason") or "—",
        )
        title += "<tr>" + "".join("<td>" + escape(c) + "</td>" for c in cells) + "</tr>"
    title += "</tbody></table>"
    title += "<p>Source: per-node OpenCode harvest manifests. Session counts "
    title += "must not be summed as token consumption. Provider free allowance "
    title += "and upstream shared quota groups remain unknown.</p></section>"
    return title
