"""Matches an incubator to a short, relevant set of startup-news / funding-database sites
(config/sources.yaml) the Scout can search with site-scoped queries. Region is inferred
from the incubator's country string; compute-vertical sources (quantum/semiconductors/
HPC/data-center) are always eligible because the whole project is compute-focused.

Kept deliberately short (a handful per incubator) so the extra discovery path doesn't
blow the Scout's tool-call budget or the free-tier LLM quota."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCES_PATH = ROOT / "config" / "sources.yaml"

# Substring (lowercased) -> region tag. First match wins.
_COUNTRY_REGION = [
    ("germany", "dach"), ("austria", "dach"), ("switzerland", "dach"),
    ("netherlands", "benelux"), ("belgium", "benelux"),
    ("sweden", "nordics"), ("norway", "nordics"), ("denmark", "nordics"), ("finland", "nordics"),
    ("france", "france"),
    ("spain", "iberia"), ("portugal", "iberia"),
    ("italy", "italy"),
    ("ireland", "uk-ireland"), ("united kingdom", "uk-ireland"), ("uk", "uk-ireland"),
    ("usa", "north-america"), ("united states", "north-america"), ("canada", "north-america"),
    ("poland", "cee"), ("czech", "cee"),
]

_COMPUTE_FOCUS = {"quantum", "semiconductors", "hpc", "datacenter"}


def load_sources() -> list[dict]:
    if not SOURCES_PATH.exists():
        return []
    with open(SOURCES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def region_for(country: str) -> str:
    c = (country or "").lower()
    for needle, region in _COUNTRY_REGION:
        if needle in c:
            return region
    return "europe"  # sensible default (most programs are European)


def sources_for(incubator: dict, sources: list[dict] | None = None, limit: int = 6) -> list[dict]:
    """Return up to `limit` relevant sources: region-matched general sites, then pan-EU/
    global general sites, then two compute-vertical sites — deduped, order-stable."""
    if sources is None:
        sources = load_sources()
    region = region_for(incubator.get("country", ""))

    region_matched = [s for s in sources if s.get("region") == region and s.get("focus") == "general"]
    broad_general = [s for s in sources if s.get("region") in ("europe", "global") and s.get("focus") == "general"]
    vertical = [s for s in sources if s.get("focus") in _COMPUTE_FOCUS]

    picked: list[dict] = []
    seen: set[str] = set()

    def add(items: list[dict], take: int) -> None:
        n = 0
        for s in items:
            if n >= take:
                break
            if s["domain"] in seen:
                continue
            seen.add(s["domain"]); picked.append(s); n += 1

    add(region_matched, 3)
    add(vertical, 2)
    add(broad_general, limit)  # fill remaining slots
    return picked[:limit]


def format_sources_for_prompt(sources: list[dict]) -> str:
    return ", ".join(f"{s['name']} (site:{s['domain']})" for s in sources)
