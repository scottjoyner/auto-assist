#!/usr/bin/env python3
"""
signal-to-assistx-bridge.py

Closes the missing link for "Signal note-to-self -> Hermes agent acts".

signal-cli-rest-api (bbernhard/signal-cli-rest-api) can fire a webhook on every
incoming message. This service receives that webhook, identifies a "note to self"
(or direct message to the registered number), and creates a READY AssistX task
addressed to the Hermes agent (hermes-local). The AssistX Hermes adapter polls
READY tasks and executes them — so Scott's note becomes an agent action
(query KG, web search, SSH remediation, etc.).

Why this exists: AssistX had POST /api/brain/signals (creates a SignalEvent in
Neo4j) but NOTHING turned a SignalEvent into a dispatched task. This bridge goes
straight to POST /api/tasks (idempotent) so the agent actually picks it up.

Deploy:
  - Configure signal-cli-rest-api webhook:
      POST /v1/accounts/<NUMBER>/webhook/set  { "url": "http://<host>:<PORT>/webhook/signal" }
  - Run:  python3 signal-to-assistx-bridge.py
  - It listens on $PORT (default 8900).

Env:
  PORT                 (default 8900)
  ASSISTX_URL          (default http://host.docker.internal:8000)
  ASSISTX_USER         (default admin)
  ASSISTX_PASS         (default change-me)
  ASSISTX_TARGET_AGENT (default hermes-local)
  SIGNAL_FROM          (registered number, e.g. +170xxxx5781; notes from this number are "note to self")
  REQUIRED_CAPS        (comma list; default terminal,web,mcp,kg)

Idempotent: uses the Signal message timestamp as idempotency_key so redeliveries
don't create duplicate tasks.
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8900"))
ASSISTX_URL = os.environ.get("ASSISTX_URL", "http://host.docker.internal:8000").rstrip("/")
ASSISTX_USER = os.environ.get("ASSISTX_USER", "admin")
ASSISTX_PASS = os.environ.get("ASSISTX_PASS", "change-me")
TARGET_AGENT = os.environ.get("ASSISTX_TARGET_AGENT", "hermes-local")
SIGNAL_FROM = os.environ.get("SIGNAL_FROM", "")
REQUIRED_CAPS = [c.strip() for c in os.environ.get("REQUIRED_CAPS", "terminal,web,mcp,kg").split(",") if c.strip()]


def _post_json(url, payload, auth=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    if auth:
        import base64
        tok = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {tok}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def create_assistx_task(text, signal_ts, source):
    """Create a READY task for the Hermes agent. Idempotent on signal_ts."""
    if not text or not text.strip():
        return {"ok": False, "error": "empty message"}
    title = text.strip()[:200]
    payload = {
        "source": "signal_note_to_self",
        "text": text.strip(),
        "signal_timestamp": signal_ts,
        "signal_source": source,
        "correlation_id": f"signal:{signal_ts}",
    }
    body = {
        "title": f"[Signal] {title}",
        "task_type": "task",
        "status": "READY",
        "kind": "signal_note_to_self",
        "target_agent_id": TARGET_AGENT,
        "required_capabilities": REQUIRED_CAPS,
        "priority": "MEDIUM",
        "payload": payload,
        "idempotency_key": f"signal-note-{signal_ts}",
    }
    status, resp = _post_json(f"{ASSISTX_URL}/api/tasks", body,
                              auth=(ASSISTX_USER, ASSISTX_PASS))
    return {"ok": status < 400, "status": status, "response": resp[:400]}


def parse_envelope(env):
    """Extract (text, timestamp, source) from a signal-cli REST webhook envelope.
    Returns None if not a note-to-self / direct-to-us message we care about.
    """
    source = env.get("source") or env.get("sourceNumber") or ""
    data_msg = env.get("dataMessage") or {}
    sync_msg = env.get("syncMessage") or {}
    timestamp = (env.get("timestamp") or data_msg.get("timestamp")
                 or sync_msg.get("timestamp") or int(time.time() * 1000))
    text = data_msg.get("message") or data_msg.get("body") or ""

    # A note-to-self: source is our own registered number, OR it's a syncMessage
    # (sent from another device of the same account). Both mean "Scott wrote this".
    is_note_to_self = False
    if SIGNAL_FROM and source == SIGNAL_FROM:
        is_note_to_self = True
    if sync_msg and not data_msg:
        # syncMessage with no dataMessage often = sent-from-elsewhere echo
        is_note_to_self = True
    if not SIGNAL_FROM and source:
        # No configured number: treat any direct message as actionable.
        is_note_to_self = True

    if not is_note_to_self:
        return None
    if not text:
        return None
    return text, timestamp, source


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, {"ok": True, "service": "signal-to-assistx-bridge"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/webhook/signal":
            self._send(404, {"error": "unknown route"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            event = json.loads(raw or b"{}")
        except Exception:
            self._send(400, {"ok": False, "error": "bad json"})
            return
        # signal-cli-rest-api wraps the envelope; support both raw envelope and {envelope:...}
        env = event.get("envelope", event)
        parsed = parse_envelope(env)
        if not parsed:
            self._send(200, {"ok": True, "action": "ignored", "reason": "not a note-to-self"})
            return
        text, ts, src = parsed
        result = create_assistx_task(text, ts, src)
        self._send(200 if result.get("ok") else 502, {"ok": result.get("ok"), "result": result})

    def log_message(self, *args):
        pass  # quiet


def main():
    print(f"[bridge] listening on :{PORT}  ->  AssistX {ASSISTX_URL}  agent={TARGET_AGENT}")
    print(f"[bridge] SIGNAL_FROM={SIGNAL_FROM or '(any direct msg)'}  caps={REQUIRED_CAPS}")
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
