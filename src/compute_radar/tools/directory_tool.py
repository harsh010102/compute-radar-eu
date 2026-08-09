"""EU-Startups Directory scraper — a structured, keyword-searchable database of European
startups (https://www.eu-startups.com/directory/). Unlike the incubator portfolio pages
(often just a name + tagline), each directory listing carries country, city, a real
business description, tags, funding status, founding year, and website — so a keyword
search like "photonic" or "quantum computing" returns rich, ready-to-classify records.

Search:  /directory/?wpbdp_view=search&kw=<keyword>   (the `kw` field is full-text; `q`
alone returns the default listing, so use `kw`). Results: `.listing-title a` -> name + URL.
Profile: /directory/<slug>/ -> labeled `.wpbdp-field-*` fields.

Deterministic (requests + BeautifulSoup); no API key, no LLM. Politeness delay shared with
the rest of the scraping layer via SCRAPE_DELAY_SECONDS.
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.eu-startups.com/directory/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
_last = {"t": 0.0}


def _polite() -> None:
    gap = float(os.getenv("SCRAPE_DELAY_SECONDS", "2"))
    wait = gap - (time.time() - _last["t"])
    if wait > 0:
        time.sleep(wait)
    _last["t"] = time.time()


def search_directory(keyword: str, max_results: int = 25) -> list[dict]:
    """Return [{name, profile_url}] for a keyword full-text search.

    NOTE: the directory does naive substring matching, so short keywords match unrelated
    words ("GaN" -> "orGANisation"). Use distinctive, compute-specific terms (photonic,
    quantum computing, silicon photonics) and let the Analyst filter what slips through.
    """
    _polite()
    try:
        resp = requests.get(
            BASE,
            headers={"User-Agent": UA},
            params={"wpbdp_view": "search", "kw": keyword},
            timeout=25,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    out, seen = [], set()
    for a in soup.select(".listing-title a"):
        name = a.get_text(strip=True)
        href = a.get("href", "")
        # keep only real listing profile links, dedupe
        if name and "/directory/" in href and href.rstrip("/") != BASE.rstrip("/"):
            if href in seen:
                continue
            seen.add(href)
            out.append({"name": name, "profile_url": href})
        if len(out) >= max_results:
            break
    return out


def fetch_listing(profile_url: str) -> dict:
    """Return the structured fields of a directory profile page (best-effort)."""
    _polite()
    rec: dict = {"profile_url": profile_url}
    try:
        resp = requests.get(profile_url, headers={"User-Agent": UA}, timeout=25)
        resp.raise_for_status()
    except requests.RequestException:
        return rec
    soup = BeautifulSoup(resp.text, "lxml")
    label_map = {
        "category": "country",
        "business description": "description",
        "based in": "city",
        "tags": "tags",
        "total funding": "funding_text",
        "founded": "founded",
        "website": "website",
    }
    for field in soup.select("[class*=wpbdp-field]"):
        label_el = field.select_one(".field-label")
        value_el = field.select_one(".value")
        if not label_el or not value_el:
            continue
        label = label_el.get_text(strip=True).rstrip(":").lower()
        for needle, key in label_map.items():
            if needle in label:
                if key == "website":
                    a = value_el.select_one("a[href]")
                    rec[key] = a["href"] if a else value_el.get_text(strip=True)
                else:
                    rec[key] = value_el.get_text(" ", strip=True)
                break
    return rec


def discover(keyword: str, max_profiles: int = 10) -> list[dict]:
    """Search a keyword and fetch each result's full profile. Rich records ready to classify."""
    hits = search_directory(keyword, max_results=max_profiles)
    records = []
    for h in hits:
        rec = fetch_listing(h["profile_url"])
        rec["name"] = h["name"]
        rec["matched_keyword"] = keyword
        records.append(rec)
    return records


# --- optional CrewAI tool wrapper (so a Scout agent can query the directory too) ---
try:
    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field

    class _DirIn(BaseModel):
        keyword: str = Field(..., description="Full-text search term, e.g. 'photonic' or 'quantum computing'")

    class EUStartupsDirectoryTool(BaseTool):
        name: str = "eu_startups_directory_search"
        description: str = (
            "Search the EU-Startups Directory (a structured database of European startups) "
            "by keyword. Returns company names with country, city, description and website. "
            "Best for finding compute companies by technology term (photonic, neuromorphic, "
            "RISC-V, semiconductor, quantum, etc.)."
        )
        args_schema: type[BaseModel] = _DirIn

        def _run(self, keyword: str) -> str:
            recs = discover(keyword, max_profiles=8)
            if not recs:
                return f"No directory results for '{keyword}'."
            lines = []
            for r in recs:
                lines.append(
                    f"- {r.get('name')} ({r.get('city','?')}, {r.get('country','?')}): "
                    f"{r.get('description','')[:160]} [{r.get('website','')}]"
                )
            return "\n".join(lines)
except Exception:  # crewai not installed (e.g. local deterministic testing) - tool optional
    EUStartupsDirectoryTool = None  # type: ignore
