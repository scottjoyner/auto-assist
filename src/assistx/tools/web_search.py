
from __future__ import annotations
from typing import Dict, Any
from duckduckgo_search import DDGS
from tavily import TavilyClient
from ..config import settings

def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    if settings.tavily_api_key:
        tv = TavilyClient(api_key=settings.tavily_api_key)
        r = tv.search(query=query, max_results=max_results)
        return {"engine": "tavily", "results": r}
    else:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return {"engine": "ddg", "results": results}
