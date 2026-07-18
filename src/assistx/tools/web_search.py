
from __future__ import annotations
import os
import time
import threading
from typing import Dict, Any
from duckduckgo_search import DDGS
from tavily import TavilyClient
from ..config import settings

# duckduckgo_search performs its HTTP requests with NO socket timeout, so a
# stalled connection (server accepts but never responds) blocks forever and
# wedges the calling worker. Bound every search call with a hard wall-clock
# timeout via a worker thread so the agent loop always makes progress.
SEARCH_TIMEOUT_S = float(os.getenv("WEB_SEARCH_TIMEOUT_S", getattr(settings, "web_search_timeout_s", 6)) or 6)


def _run_with_timeout(fn, timeout: float):
    box: Dict[str, Any] = {}

    def _target():
        try:
            box["result"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    th = threading.Thread(target=_target, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        # Timed out: leave the thread running (daemon) but signal failure.
        return None, TimeoutError(f"search timed out after {timeout:.0f}s")
    if "error" in box:
        return None, box["error"]
    return box.get("result"), None


def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    if settings.tavily_api_key:
        def _tavily():
            tv = TavilyClient(api_key=settings.tavily_api_key)
            return tv.search(query=query, max_results=max_results)

        res, err = _run_with_timeout(_tavily, SEARCH_TIMEOUT_S)
        if err is None:
            return {"engine": "tavily", "results": res}
        # fall through to DDG on Tavily failure
    # DuckDuckGo HTML is frequently rate-limited (HTTP 202) or stalls entirely
    # with no socket timeout of its own. We bound it to a single short attempt:
    # if it does not respond within SEARCH_TIMEOUT_S we return an empty result
    # set so the agent loop can still complete the task without wasting minutes
    # per call. (No Tavily key is configured in this deployment.)
    def _ddg():
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    results, err = _run_with_timeout(_ddg, SEARCH_TIMEOUT_S)
    if err is None:
        return {"engine": "ddg", "results": results}
    return {"engine": "ddg", "results": [], "error": f"search unavailable: {err}"}
