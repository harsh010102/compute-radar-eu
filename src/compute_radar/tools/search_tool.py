"""Web search for the Scout agent. Two backends, chosen at call time:

  1. **Exa** semantic search (primary) — set EXA_API_KEY (free tier at https://exa.ai).
     Neural/auto search returns far more relevant startup-discovery results than scraping a
     SERP, and it's a stable keyed API rather than fragile HTML.
  2. **DuckDuckGo HTML** (fallback, no key) — used whenever EXA_API_KEY is unset or an Exa
     call fails, so the pipeline still runs for free with zero configuration.

Both return the same `- title / url / snippet` text to the agent, so the rest of the
pipeline is backend-agnostic (swap either without touching agents/tasks). See decision.md
D14 (Exa primary + DDG fallback).
"""

from __future__ import annotations

import os

import requests
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

USER_AGENT = "compute-radar-eu/0.1 (+https://github.com/; research/non-commercial scraping)"
EXA_SEARCH_URL = "https://api.exa.ai/search"


def _exa_search(query: str) -> str | None:
    """Exa semantic search. Returns formatted results, or None if no key is configured.
    Raises requests.RequestException on a transport/HTTP error so the caller can fall back."""
    key = os.getenv("EXA_API_KEY")
    if not key:
        return None
    resp = requests.post(
        EXA_SEARCH_URL,
        headers={"x-api-key": key, "Content-Type": "application/json"},
        json={
            "query": query,
            "numResults": 10,
            "type": "auto",  # let Exa pick neural vs keyword per query
            "contents": {"text": {"maxCharacters": 350}},
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []
    for r in data.get("results", []):
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = " ".join((r.get("text") or "").split())[:300]
        if title or url:
            results.append(f"- {title}\n  {url}\n  {snippet}")
    return "\n".join(results) if results else None


def _ddg_search(query: str) -> str:
    """Keyless DuckDuckGo HTML scrape. The most fragile backend (breaks if DDG changes its
    markup) - kept only as the no-key fallback for Exa."""
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return f"SEARCH_ERROR: {exc}. Fall back to fetch_page on known incubator URLs."

    soup = BeautifulSoup(resp.text, "lxml")
    results = []
    for result in soup.select(".result")[:10]:
        title_el = result.select_one(".result__title")
        snippet_el = result.select_one(".result__snippet")
        link_el = result.select_one(".result__url")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        link = link_el.get_text(strip=True) if link_el else ""
        results.append(f"- {title}\n  {link}\n  {snippet}")

    if not results:
        return f"No results parsed for '{query}'. DuckDuckGo markup may have changed."
    return "\n".join(results)


class WebSearchInput(BaseModel):
    query: str = Field(..., description="Search query, e.g. 'imec.istart 2026 cohort companies'")


class FreeWebSearchTool(BaseTool):
    name: str = "free_web_search"
    description: str = (
        "Best-effort web search. Returns up to 10 title/url/snippet results. Use this only "
        "when a startup isn't already listed on the incubator's own portfolio page - prefer "
        "fetch_page against the program's site first."
    )
    args_schema: type[BaseModel] = WebSearchInput

    def _run(self, query: str) -> str:
        if os.getenv("EXA_API_KEY"):
            try:
                out = _exa_search(query)
                if out:
                    return out
            except requests.RequestException:
                pass  # Exa unavailable/errored - fall back to the keyless backend
        return _ddg_search(query)
