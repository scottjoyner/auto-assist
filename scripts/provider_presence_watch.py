#!/usr/bin/env python3
"""Low-cost provider presence monitor. Never requests inference or generates tokens.

One catalog/health endpoint per provider; status is NOT proof of usable inference.
No paid or free-model canaries are automatically dispatched.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
DEFAULT_STATE = ACCOUNT_HOME / ".local/state/auto-assist/provider-presence.json"
PROVIDERS = (
    {"id": "openrouter", "kind": "http", "url": "https://openrouter.ai/api/v1/models",
     "env": "OPENROUTER_API_KEY", "auth_required": False, "min_interval": 1800},
    {"id": "kilo_anonymous", "kind": "http",
     "url": "https://api.kilo.ai/api/openrouter/models",
     "public_bearer": "anonymous", "min_interval": 1800},
    {"id": "cohere_direct", "kind": "http", "url": "https://api.cohere.com/v1/models?page_size=1",
     "env": "COHERE_API_KEY", "auth_required": True, "min_interval": 1800},
    {"id": "grok_xai_api", "kind": "http", "url": "https://api.x.ai/v1/models",
     "env": "XAI_API_KEY", "auth_required": True, "min_interval": 1800,
     "note": "API free-token eligibility unverified; metadata-only"},
    {"id": "omniroute", "kind": "http",
     "url": "http://127.0.0.1:20128/api/monitoring/health", "min_interval": 900},
    {"id": "auto_router", "kind": "http",
     "url": "http://127.0.0.1:8088/health", "min_interval": 900},
    {"id": "local_lmstudio", "kind": "http",
     "url": "http://127.0.0.1:1234/v1/models", "min_interval": 900},
    {"id": "opencode_cli", "kind": "binary", "min_interval": 900},
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _probe_http(provider: dict, env: dict) -> dict:
    key_name = provider.get("env")
    credential = env.get(key_name, "").strip() if key_name else ""
    if provider.get("auth_required") and not credential:
        return {"status": "credential_missing", "http_code": None,
                "delay": provider["min_interval"]}
    headers = {"User-Agent": "fleet-provider-presence/1.0", "Accept": "application/json"}
    if credential:
        headers["Authorization"] = "Bearer " + credential
    elif provider.get("public_bearer"):
        headers["Authorization"] = "Bearer " + provider["public_bearer"]
    url = provider["url"]
    assert url.startswith("https://") or url.startswith("http://127.0.0.1:")
    req = Request(url, headers=headers, method="GET")
    started = time.monotonic()
    try:
        with build_opener(NoRedirect()).open(req, timeout=4) as response:
            response.read(1024)  # bounded, no response body persisted
            code = response.status
            latency = round((time.monotonic() - started) * 1000)
            return {"status": "catalog_reachable" if code == 200 else "unexpected_http",
                    "http_code": code, "latency_ms": latency,
                    "delay": provider["min_interval"]}
    except HTTPError as exc:
        code = exc.code
        delay = 3600 if code in (429, 503) else 86400 if code in (401, 402, 403) else provider["min_interval"]
        try:
            retry_after = int(exc.headers.get("Retry-After", "0"))
            delay = max(delay, min(retry_after, 86400))
        except (ValueError, TypeError, AttributeError):
            pass
        return {"status": "rate_limited" if code in (429, 503) else
                "auth_or_billing_blocked" if code in (401, 402, 403) else "http_error",
                "http_code": code, "delay": delay}
    except (URLError, TimeoutError, OSError) as exc:
        return {"status": "unreachable", "http_code": None,
                "error_type": type(exc).__name__, "delay": provider["min_interval"]}


def _probe_binary(_provider: dict, _env: dict) -> dict:
    candidates = [ACCOUNT_HOME / ".opencode/bin/opencode",
                  Path.home() / ".opencode/bin/opencode"]
    exe = next((str(p) for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
    exe = exe or shutil.which("opencode")
    if not exe:
        return {"status": "unreachable", "delay": 900}
    try:
        result = subprocess.run([exe, "--version"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=4, text=True)
        return {"status": "cli_ready" if result.returncode == 0 else "unreachable",
                "delay": 900}
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unreachable", "delay": 900}


def check_one(provider: dict, previous: dict, *, now: float,
              env: dict, http_probe=None, binary_probe=None) -> dict:
    due = previous.get("next_due_epoch", 0)
    if now < due:
        return dict(previous)
    probe = (binary_probe or _probe_binary) if provider["kind"] == "binary" else (http_probe or _probe_http)
    result = probe(provider, env)
    status = result["status"]
    record = {
        "status": status, "checked_epoch": round(now, 3),
        "next_due_epoch": round(now + max(60, int(result.get("delay", provider["min_interval"]))), 3),
        "endpoint": provider.get("url", "local CLI"),
        "probe": "catalog_or_health_only" if provider["kind"] == "http" else "cli_version_only",
        "inference_qualified": False,
    }
    if status in ("catalog_reachable", "cli_ready"):
        record["last_reachable_epoch"] = round(now, 3)
    elif "last_reachable_epoch" in previous:
        record["last_reachable_epoch"] = previous["last_reachable_epoch"]
    for key in ("http_code", "latency_ms", "error_type"):
        if key in result:
            record[key] = result[key]
    if provider.get("note"):
        record["note"] = provider["note"]
    return record


def run(state_path: Path = DEFAULT_STATE, providers=PROVIDERS, *,
        now: float | None = None, env: dict | None = None,
        http_probe=None, binary_probe=None) -> dict:
    now = time.time() if now is None else now
    env = os.environ if env is None else env
    state_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(state_path.parent, 0o700)
    lock_path = state_path.with_suffix(".lock")
    with lock_path.open("a+") as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "already_running", "providers": {}}
        try:
            previous = json.loads(state_path.read_text())
        except (OSError, ValueError):
            previous = {}
        prior = previous.get("providers", {})
        new = {}
        for provider in providers:
            name = provider["id"]
            new[name] = check_one(provider, prior.get(name, {}), now=now, env=env,
                                  http_probe=http_probe, binary_probe=binary_probe)
        result = {"schema": "provider-presence/v1", "updated_epoch": round(now, 3),
                  "providers": new, "inference_probe_requests": 0,
                  "auto_generated_tokens": 0}
        tmp = state_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, state_path)
        finally:
            tmp.unlink(missing_ok=True)
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    result = run(args.state)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["providers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
