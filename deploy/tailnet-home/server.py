#!/usr/bin/env python3
"""Tailnet-only AssistX landing page. No API proxy, auth bypass, or remote embeds."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import URLError
from html import escape
import json

SERVICES = [
    ("AssistX API", "/health", "Authenticated AssistX control-plane health", "assistx"),
    ("AssistX identity", "/api/v1/auth/whoami", "Identity and authorization endpoint", None),
    ("AssistX model catalog", "/api/v1/runtime/catalog", "Authenticated model/route inventory", None),
    ("Kipnerter API test health", "/kipnerter-api-health", "Staging/recovery only — not production", "kipnerter_test"),
    ("Kipnerter production node", "https://kipnerter-prod-01-1.tailcb8954.ts.net/",
     "TLS currently requires repair; do not treat as healthy", None),
    ("Kipnerter iOS development", "https://github.com/scottjoyner/kipnerter-ios/pull/358",
     "Release/graph acceptance tracking", None),
    ("Fleet knowledge handoff", "https://github.com/scottjoyner/knowledge/pull/65",
     "Change history and explicit release gates", None),
]
PROBES = {"assistx": "http://127.0.0.1:8000/health",
          "kipnerter_test": "http://127.0.0.1:18081/health"}
def state():
    result = {}
    for name, url in PROBES.items():
        try:
            with urlopen(url, timeout=1.2) as response:
                result[name] = "online" if response.status == 200 else "degraded"
        except (OSError, URLError, TimeoutError):
            result[name] = "unavailable"
    return result

class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.respond(head_only=True)
    def do_GET(self):
        self.respond(head_only=False)
    def respond(self, head_only=False):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            checks = state()
            cards = "".join(
                '<article><h2><a href="%s">%s ↗</a></h2><p>%s</p><span class="%s">%s</span></article>'
                % (escape(url, quote=True), escape(title), escape(description),
                   escape(checks.get(key, "link")), escape(checks.get(key, "link")))
                for title, url, description, key in SERVICES
            )
            body = ('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="120"><title>AssistX · Tailnet Services</title>
<style>body{font:16px system-ui,sans-serif;margin:0;background:#101923;color:#e9edf1}
main{max-width:1000px;margin:6vh auto;padding:0 22px}header{margin-bottom:35px}
small{color:#a1b2c0}h1{font-size:clamp(2rem,5vw,3rem);margin-bottom:.3em}
section{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}
article{background:#1b2936;border:1px solid #354657;border-radius:14px;padding:18px}
article h2{font-size:1.1rem;margin:0}a{color:#b5ddff;text-decoration:none}a:hover{text-decoration:underline}
p{color:#b9c4ce;min-height:44px;line-height:1.5}.online{color:#83dfa5}
.unavailable,.degraded{color:#f4b08d}.link{color:#97a9b8}span{font-size:.85rem}
footer{color:#9eb2c2;margin:36px 0;line-height:1.6}</style></head><body><main>
<header><small>PRIVATE TAILNET DIRECTORY · NO PUBLIC PROXY</small>
<h1>AssistX Services</h1><p>One place for fleet routes, health and release evidence.</p></header>
<section>''' + cards + '''</section><footer>Checks refresh every 120 seconds. Service links retain
their own authentication. Production Kipnerter TLS is not yet accepted.
Private services are not proxied through this directory.</footer></main></body></html>''').encode()
            self.send(200, "text/html; charset=utf-8", body, head_only)
        elif path in ("/healthz", "/status.json"):
            body = json.dumps({"status": "ok", "services": state()}).encode()
            self.send(200, "application/json", body, head_only)
        elif path == "/kipnerter-api-health":
            body = json.dumps({"service": "kipnerter-api", "environment": "test",
                               "status": state()["kipnerter_test"]}).encode()
            self.send(200, "application/json", body, head_only)
        else:
            self.send(404, "text/plain", b"Not found", head_only)
    def send(self, code, mime, body, head_only):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)
    def log_message(self, fmt, *args):
        return

if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8081), Handler)
    server.serve_forever()
