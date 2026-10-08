"""Synthetic Chromium acceptance for AssistX trace investigations.

All page and API requests are fulfilled in-process; no server, login,
provider API or fleet command is touched. Requires Python Playwright
and an installed Chromium executable.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/traces.html").read_text()
HTML = re.sub(r"\{\{ url_for\('static', path='([^']+)'\) \}\}", r"/static/\1", HTML)
mode = {"index": 200, "detail": 200}
calls = []

def detail(cid):
    return {"correlation_id": cid, "current_state": "synthetic",
            "events": [{"event_type": "router.started", "source": "mock-node",
                        "task_id": "mock-task", "ts_ms": 1791478800000,
                        "payload_json": json.dumps({"CANARY_SECRET": "SYNTHETIC_ONLY", "id": cid})},
                       {"event_type": "dispatch.accepted", "source": "mock-node",
                        "task_id": "mock-task", "ts_ms": 1791478801000}],
            "context": {"schema": "trace-context-v1", "truncated": [],
                        "fields": {"task_id": [{"value": "mock-task", "events": 2,
                                                 "provenance": "trace_event_property"}],
                                   "source": [], "dispatch_id": [], "route_id": [],
                                   "assignment_id": []}}}

def evidence(cid):
    return {"schema": "trace-task-evidence-v1", "correlation_id": cid,
            "trust": "unverified_correspondence_not_execution_attestation",
            "source_authenticated": False, "node_or_agent_verified": False,
            "graph_write_permitted": False, "truncated": False,
            "tasks": [{"task_id": "mock-task", "node_id": "mock-node",
                       "worker_id": "unverified-worker", "task_status": "queued",
                       "registry_state": "registry_id_match_unverified", "registry_matches": 1,
                       "task_provenance": "trace_event_relationship_and_property",
                       "projection_provenance": "assignment_projection_unverified",
                       "node_or_agent_verified": False}]}

def route(r):
    request = r.request
    path = urlsplit(request.url).path
    calls.append((request.method, path))
    if request.method != "GET":
        return r.fulfill(status=405, body="read-only fixture")
    if path == "/traces":
        return r.fulfill(status=200, content_type="text/html", body=HTML)
    if path.startswith("/static/"):
        f = ROOT / path.lstrip("/")
        if not f.is_file() or not f.resolve().is_relative_to((ROOT / "static").resolve()):
            return r.fulfill(status=404, body="missing fixture file")
        mimetype = "text/css" if path.endswith(".css") else "application/javascript"
        return r.fulfill(status=200, content_type=mimetype, body=f.read_bytes())
    if path == "/api/traces":
        if mode["index"] != 200:
            return r.fulfill(status=mode["index"], body="{}")
        rows = [{"correlation_id": cid, "outcome": "completed", "events": 1,
                 "duration_ms": 300, "last_ts_ms": 1791478800000}
                for cid in ("one", "two")]
        outcome = parse_qs(urlsplit(request.url).query).get("outcome", ["all"])[0]
        if outcome != "all":
            rows = [{**row, "outcome": outcome} for row in rows]
        return r.fulfill(status=200, content_type="application/json",
                         body=json.dumps({"total": 82, "traces": rows, "outcome": outcome}))
    if path.startswith("/api/traces/") and path.endswith("/evidence"):
        cid = unquote(path.removeprefix("/api/traces/").removesuffix("/evidence"))
        return r.fulfill(status=200, content_type="application/json",
                         body=json.dumps(evidence(cid)))
    if path.startswith("/api/traces/"):
        if mode["detail"] != 200:
            return r.fulfill(status=mode["detail"], body="{}")
        cid = unquote(path.removeprefix("/api/traces/"))
        return r.fulfill(status=200, content_type="application/json",
                         body=json.dumps(detail(cid)))
    return r.fulfill(status=404, body="staged")

def check(value, description):
    if not value:
        raise AssertionError(description)
    print("PASS " + description, flush=True)

def main():
    with sync_playwright() as pw:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        kwargs = {"headless": True, "args": ["--no-sandbox"]}
        if executable:
            kwargs["executable_path"] = executable
        browser = pw.chromium.launch(**kwargs)
        for width, height in ((375, 812), (768, 1024), (1440, 900)):
            mode.update(index=200, detail=200)
            page = browser.new_page(viewport={"width": width, "height": height})
            page.route("**/*", route)
            page.goto("http://127.0.0.1:8765/traces")
            page.locator(".trace-row").first.wait_for()
            page.wait_for_function(
                "() => document.querySelector('#trace-detail').textContent.includes('router.started')")
            overflowing = page.evaluate("() => document.documentElement.scrollWidth > innerWidth + 1")
            check(not overflowing, f"{width}px no horizontal overflow")
            check(page.locator("a[href='/provider-usage']").count() == 0,
                  f"{width}px provider route remains staged")
            page.close()

        page = browser.new_page(viewport={"width": 375, "height": 812})
        page.route("**/*", route)
        page.goto("http://127.0.0.1:8765/traces")
        second = page.locator(".trace-row").nth(1)
        second.wait_for()
        second.focus()
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => document.querySelector('#trace-detail').textContent.includes('router.started')")
        check(page.evaluate("() => document.activeElement?.dataset?.cid === 'two'"),
              "Enter selection preserves keyboard focus")
        check(page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle !== 'none'"),
              "keyboard focus has visible outline")

        check("CANARY_SECRET" not in page.locator("#trace-detail").inner_text(),
              "collapsed payload not in rendered DOM")
        disclosure = page.locator("#trace-detail details").first
        calls_before_disclosure = len(calls)
        disclosure.locator("summary").click()
        page.wait_for_function(
            "() => document.querySelector('#trace-detail details pre').textContent.includes('CANARY_SECRET')")
        check(len(calls) == calls_before_disclosure,
              "event disclosure issues no additional API request")
        check(True, "deliberate disclosure renders event data")
        disclosure.locator("summary").click()
        page.wait_for_function(
            "() => document.querySelector('#trace-detail details pre').textContent === ''")
        check(True, "closing disclosure removes event data")

        context = page.locator('button.trace-context-chip[data-context-field="task_id"]').first
        check(context.count() == 1, "recorded context contains bounded task chip")
        before = len(calls)
        context.click()
        check(len(calls) == before, "context navigation makes no extra API request")
        check(page.evaluate("() => document.activeElement?.classList.contains('trace-context-chip')"),
              "context filter preserves keyboard target")
        check(page.locator('.trace-clear-context').count() == 1,
              "context filter has explicit reset")
        page.locator('.trace-clear-context').click()
        check(page.locator('.trace-timeline .trace-event').count() == 2,
              "reset restores all loaded events")
        evidence_before = sum(p.endswith('/evidence') for _, p in calls)
        check(evidence_before == 0, "task evidence is not fetched automatically")
        page.locator('button.trace-inspect-evidence').click()
        page.wait_for_function("() => document.querySelector('.trace-evidence-results').textContent.includes('identity unverified')")
        check(sum(p.endswith('/evidence') for _, p in calls) == evidence_before + 1,
              "task evidence fetched once on explicit operator action")
        check('not execution, node, or agent attestation' in page.locator('.trace-evidence-results').inner_text(),
              "registry ID match explicitly denies execution attestation")

        page.goto("http://127.0.0.1:8765/traces?outcome=failed&trace=filtered-old")
        page.wait_for_function(
            "() => document.querySelector('#trace-detail').textContent.includes('filtered-old')")
        check(page.locator('#trace-outcome').input_value() == 'failed',
              "global outcome selection survives a deep link")
        check('outcome=failed' in page.locator('#trace-permalink').get_attribute('href'),
              "global filtered permalink retains outcome context")
        page.goto("http://127.0.0.1:8765/traces?trace=outside-first-page")
        page.wait_for_function(
            "() => document.querySelector('#trace-detail').textContent.includes('outside-first-page')")
        check(any(p == "/api/traces/outside-first-page" for _, p in calls),
              "direct link fetches outside-page detail")

        mode["index"] = 401
        page.locator("#trace-refresh").click()
        page.wait_for_function(
            "() => document.querySelector('#trace-status').textContent.includes('Authentication')")
        check(page.locator("#trace-copy").is_disabled(), "401 disables copy")
        check("outside-first-page" not in page.locator("#trace-detail").inner_text(),
              "401 clears stale detail")

        for transient_status in (429, 503):
            mode["index"] = transient_status
            page.locator("#trace-refresh").click()
            page.wait_for_function("() => document.querySelector('#trace-status').textContent.includes('temporarily unavailable')")
            check(page.locator("#trace-retry").count() == 1,
                  f"HTTP {transient_status} exposes index retry")
            mode["index"] = 200
            page.locator("#trace-retry").click()
            page.locator(".trace-row").first.wait_for()
            check(True, f"HTTP {transient_status} retry restores trace index")

        mode.update(index=200, detail=503)
        # A new selection must first experience the 503 before its Refresh can retry it.
        page.goto("http://127.0.0.1:8765/traces")
        page.locator(".trace-row").first.wait_for()
        page.wait_for_function(
            "() => document.querySelector('#trace-detail').textContent.includes('Timeline unavailable')")
        mode["detail"] = 200
        page.locator("#trace-refresh").click()
        page.wait_for_function(
            "() => document.querySelector('#trace-detail').textContent.includes('router.started')")
        check(True, "Refresh retries failed detail after 503")
        check(all(method == "GET" for method, _ in calls),
              "no non-GET request from trace UI")
        browser.close()
    print("BROWSER_SYNTHETIC_ACCEPTANCE_COMPLETE", flush=True)

if __name__ == "__main__":
    main()
