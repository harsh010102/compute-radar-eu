"""Web search for the Scout agent. Two backends, chosen at call time:

  1. **Exa** semantic search (primary) — set EXA_API_KEY (free tier at https://exa.ai).
     Neural/auto search returns far more relevant startup-discovery results than scraping a
     SERP, and it's a stable keyed API rather than fragile HTML.
  2. **DuckDuckGo HTML** (fallback, no key) — used whenever EXA_API_KEY is unset, the free
     Exa budget is spent, or an Exa call fails, so the pipeline still runs for free.

Free-tier discipline (see decision.md D14): we call Exa in its cheapest mode (search only, no
`contents` retrieval — the Scout reads pages with its own free scraper), cap calls per run,
and the moment Exa returns 401/402/429 (key rejected / billing / quota) we disable it for the
rest of the process and fall back to DuckDuckGo. Net effect: Exa is used *while it's free* and
never generates a paid call. Both backends return the same `- title / url / snippet` text, so
agents/tasks are backend-agnostic.
"""

from __future__ import annotations

import os
import sys

import requests
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

USER_AGENT = "compute-radar-eu/0.1 (+https://github.com/; research/non-commercial scraping)"
EXA_SEARCH_URL = "https://api.exa.ai/search"

# Per-process state so we stop paying/erroring the instant Exa's free budget is spent.
_exa_state = {"disabled": False, "calls": 0}


def _exa_budget_left() -> bool:
    if _exa_state["disabled"]:
        return False
    cap = int(os.getenv("EXA_MAX_CALLS_PER_RUN", "300"))
    return _exa_state["calls"] < cap


def _exa_search(query: str) -> str | None:
    """Exa search (cheapest mode: no `contents`). Returns formatted results, or None if no
    key. Raises requests.RequestException on any HTTP error; on 401/402/429 it also disables
    Exa for the rest of the run so we never retry a paid/blocked call."""
    key = os.getenv("EXA_API_KEY")
    if not key:
        return None
    resp = requests.post(
        EXA_SEARCH_URL,
        headers={"x-api-key": key, "Content-Type": "application/json"},
        # search only, no `contents`: the Scout fetches promising URLs with its own free
        # scraper, so we don't pay Exa for content retrieval on top of the search.
        json={"query": query, "numResults": 10, "type": "auto"},
        timeout=20,
    )
    _exa_state["calls"] += 1
    if resp.status_code in (401, 402, 429):
        _exa_state["disabled"] = True  # key rejected / billing / quota -> stop using Exa
        raise requests.HTTPError(f"Exa {resp.status_code} (key/billing/quota) - disabling Exa for this run")
    resp.raise_for_status()
    data = resp.json()
    results = []
    for r in data.get("results", []):
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        if not (title or url):
            continue
        snippet = " ".join((r.get("text") or "").split())[:300]
        results.append(f"- {title}\n  {url}" + (f"\n  {snippet}" if snippet else ""))
    return "\n".join(results) if results else None


def _ddg_search(query: str) -> str:
    """Keyless DuckDuckGo HTML scrape. The most fragile backend (breaks if DDG changes its
    markup) - kept only as the no-key / budget-spent fallback for Exa."""
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
        if os.getenv("EXA_API_KEY") and _exa_budget_left():
            try:
                out = _exa_search(query)
                if out:
                    print(f"[free_web_search] exa: {query[:60]}", file=sys.stderr)
                    return out
            except requests.RequestException as exc:
                print(f"[free_web_search] exa unavailable ({exc}); DuckDuckGo fallback", file=sys.stderr)
        print(f"[free_web_search] duckduckgo: {query[:60]}", file=sys.stderr)
        return _ddg_search(query)
