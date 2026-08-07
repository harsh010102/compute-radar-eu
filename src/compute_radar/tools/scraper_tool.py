"""Free web-scraping tool for the Scout agent: plain requests + BeautifulSoup, no paid
search/scraping API. Deliberately simple - these are mostly static institutional pages,
not JS-heavy SPAs, so a headless browser is unnecessary overhead for this use case.

If you later hit a site that renders its portfolio list client-side, swap this for
crewai_tools.ScrapeWebsiteTool (Selenium-backed) or Playwright rather than complicating
this one - keep the free/no-key path working for everything else.
"""

from __future__ import annotations

import os
import time

import requests
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

USER_AGENT = "compute-radar-eu/0.1 (+https://github.com/; research/non-commercial scraping)"
_last_request_at: dict[str, float] = {}


def _polite_delay(url: str) -> None:
    """Enforce SCRAPE_DELAY_SECONDS between requests to the same host."""
    from urllib.parse import urlparse

    host = urlparse(url).netloc
    delay = float(os.getenv("SCRAPE_DELAY_SECONDS", "2"))
    last = _last_request_at.get(host, 0.0)
    wait = delay - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    _last_request_at[host] = time.time()


class FetchPageInput(BaseModel):
    url: str = Field(..., description="Full URL to fetch, e.g. https://imecistart.com/en/portfolio")


class FetchPageTool(BaseTool):
    name: str = "fetch_page"
    description: str = (
        "Fetch a web page and return its cleaned, readable text plus every link on the "
        "page (text -> href). Use this to read an incubator's portfolio/cohort page, then "
        "follow links that look like individual startup profiles."
    )
    args_schema: type[BaseModel] = FetchPageInput

    def _run(self, url: str) -> str:
        _polite_delay(url)
        try:
            resp = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=20, allow_redirects=True
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            return f"FETCH_ERROR fetching {url}: {exc}"

        soup = BeautifulSoup(resp.text, "lxml")

        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        # Collapse excessive blank lines
        lines = [ln for ln in text.splitlines() if ln.strip()]
        # Capped hard: free-tier OpenRouter endpoints often carry a much smaller context
        # window than their paid counterparts, and this pipeline is designed to run
        # multiple tool calls per task - keep each one cheap so they don't compound into
        # an overflow that silently comes back as an empty LLM response.
        text = "\n".join(lines)[:4000]

        links = []
        for a in soup.find_all("a", href=True):
            label = a.get_text(strip=True)
            href = a["href"]
            if label and href and not href.startswith(("javascript:", "mailto:", "#")):
                links.append(f"{label} -> {href}")
        links_block = "\n".join(links[:40])

        return f"PAGE TEXT ({url}):\n{text}\n\nLINKS ON PAGE:\n{links_block}"
