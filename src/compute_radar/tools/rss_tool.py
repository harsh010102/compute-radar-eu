"""Deterministic RSS/Atom ingestion of the regional + compute-vertical startup-news sources
in config/sources.yaml.

This is the "don't scrape Crunchbase - read what Crunchbase reads" path (decision.md D2/D32):
a Swiss seed round or a French cohort announcement is published on a regional ticker
(Startupticker.ch, Maddyness, ArcticStartup, Silicon Canals, ...) *before* it reaches a VC
database. We pull each source's feed, keyword-filter for compute relevance, and hand the
survivors to the Analyst - the same deterministic-scrape-then-LLM-classify shape as
directory_tool.py / discover_from_directory.py, but reliable (a real feed) instead of a
fragile site-scoped SERP search.

Feed URL resolution: a source may set an explicit `rss:` in sources.yaml; otherwise we try
https://<domain>/feed/, https://www.<domain>/feed/, then https://<domain>/rss/ and use the
first that parses with entries. feedparser tolerates RSS vs Atom and messy date formats.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from bs4 import BeautifulSoup

UA = "compute-radar-eu/0.1 (+https://github.com/; research/non-commercial)"
_last = {"t": 0.0}

# Compute-relevance keyword net (lowercased substring match on title + summary). Loose on
# purpose - the skeptical Analyst is the backstop that drops false positives (a fintech named
# "Quantum...", a policy roundup, big-corp news). Ambiguous single words like "silicon" or
# "processor" are omitted deliberately: they flood the feed with non-startup noise.
COMPUTE_KEYWORDS = [
    "photonic", "quantum", "chiplet", "interconnect", "semiconductor", "wafer",
    "lithography", "neuromorphic", "risc-v", "spintronic", "cryogenic", "hbm",
    "advanced packaging", "co-design", "liquid cooling", "immersion cooling",
    "power delivery", "gallium nitride", "silicon carbide", "compound semiconductor",
    "sovereign cloud", "data center", "datacenter", "ai chip", "chip startup",
    "asic", "fpga", "memristor", "in-memory", "silicon photonics",
    "optical interconnect", "qubit", "spin qubit",
]


def _polite() -> None:
    gap = float(os.getenv("SCRAPE_DELAY_SECONDS", "2"))
    wait = gap - (time.time() - _last["t"])
    if wait > 0:
        time.sleep(wait)
    _last["t"] = time.time()


def _candidate_urls(source: dict) -> list[str]:
    rss = source.get("rss")
    if rss:
        return [rss]
    d = source["domain"]
    return [f"https://{d}/feed/", f"https://www.{d}/feed/", f"https://{d}/rss/"]


def _fetch_raw(url: str) -> bytes | None:
    _polite()
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return None


def fetch_feed(source: dict) -> tuple[list, str | None]:
    """Return (entries, feed_url) for the first candidate URL that parses with entries."""
    for url in _candidate_urls(source):
        raw = _fetch_raw(url)
        if not raw:
            continue
        parsed = feedparser.parse(raw)
        if parsed.entries:
            return parsed.entries, url
    return [], None


def _entry_dt(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _clean(html: str) -> str:
    return BeautifulSoup(html or "", "lxml").get_text(" ", strip=True)


def _is_compute(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in COMPUTE_KEYWORDS)


def discover_news(
    sources: list[dict],
    since_days: int = 120,
    max_per_feed: int = 40,
    max_total: int = 120,
) -> list[dict]:
    """Pull recent, compute-relevant entries across all given sources.

    Returns rich records ready to hand to the Analyst:
    [{title, summary, link, source, published}]. Deduped by link; newest-first per feed as
    the feeds themselves order them.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    out: list[dict] = []
    seen: set[str] = set()
    for src in sources:
        entries, _ = fetch_feed(src)
        taken = 0
        for e in entries:
            if taken >= max_per_feed:
                break
            link = (e.get("link") or "").strip()
            title = (e.get("title") or "").strip()
            summary = e.get("summary") or ""
            if not link or link in seen:
                continue
            dt = _entry_dt(e)
            if dt and dt < cutoff:
                continue
            if not _is_compute(f"{title} {summary}"):
                continue
            seen.add(link)
            out.append({
                "title": title,
                "summary": _clean(summary)[:400],
                "link": link,
                "source": src.get("name", src["domain"]),
                "published": dt.date().isoformat() if dt else None,
            })
            taken += 1
            if len(out) >= max_total:
                return out
    return out
