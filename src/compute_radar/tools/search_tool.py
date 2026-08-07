"""Free, no-API-key web search for the Scout agent, used only to discover startups that
aren't linked from a program's own portfolio page (e.g. a press-release-only spinout).

This scrapes DuckDuckGo's HTML endpoint, which has no official API and no key requirement -
by the same token it's the most fragile part of this pipeline and will break if DuckDuckGo
changes its markup. If that happens, or if you want more reliable results, swap this for
a free-tier key-based option (Tavily and Serper both have free monthly quotas) - the rest
of the pipeline doesn't care which search tool the Scout agent has, as long as it returns
a title/url/snippet list.
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

USER_AGENT = "compute-radar-eu/0.1 (+https://github.com/; research/non-commercial scraping)"


class WebSearchInput(BaseModel):
    query: str = Field(..., description="Search query, e.g. 'imec.istart 2026 cohort companies'")


class FreeWebSearchTool(BaseTool):
    name: str = "free_web_search"
    description: str = (
        "Best-effort free web search (no API key). Returns up to 10 title/url/snippet "
        "results. Use this only when a startup isn't already listed on the incubator's own "
        "portfolio page - prefer fetch_page against the program's site first."
    )
    args_schema: type[BaseModel] = WebSearchInput

    def _run(self, query: str) -> str:
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
