"""Keyword-driven discovery from the EU-Startups Directory.

Distinct from the per-incubator Scout: instead of "who came through program X", this asks
"which compute companies exist under keyword Y" against a structured startup database
(tools/directory_tool.py). The deterministic scrape produces rich records (name, country,
city, description, tags, website); the Analyst LLM then filters out the false positives the
keyword search inevitably returns (e.g. "Quantum Charging" = EV charging, not quantum) and
classifies the survivors against the taxonomy. Results are stored under the
`eu-startups-directory` bucket and merged with the same additive merge as the main pipeline.

    python -m compute_radar.discover_from_directory              # default compute keywords
    python -m compute_radar.discover_from_directory --keyword photonic --keyword GaN
    python -m compute_radar.discover_from_directory --max 30     # cap companies classified

Runs on whichever LLM provider is configured (OpenRouter/Gemini round-robin). No-op-safe:
if no provider is set it scrapes and reports but skips classification.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from compute_radar.tools.directory_tool import discover

# The directory search is weak: it does naive SINGLE-TOKEN substring matching. Short/common
# terms return junk ("GaN" -> "orGANisation"); multi-word phrases ("quantum computing",
# "silicon photonics") return nothing. Only distinctive single words carry signal - in
# practice "photonic" and "quantum" are the strong ones (~11 real hits each), with a few
# smaller ones. The Analyst is the backstop that drops false positives the keyword still
# lets through (e.g. a fintech called "QuantumScale", an EV charger "Quantum Charging").
# `semiconductor` currently returns 0 but is harmless and future-proofs coverage.
DEFAULT_KEYWORDS = [
    "photonic", "quantum", "silicon", "processor", "nanotechnology", "semiconductor",
]

ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = ROOT / "data" / "startups.json"
INCUBATOR_ID = "eu-startups-directory"


def _scrape(keywords: list[str], max_companies: int, per_keyword: int) -> list[dict]:
    by_url: dict[str, dict] = {}
    for kw in keywords:
        print(f"  searching '{kw}'...", file=sys.stderr)
        for rec in discover(kw, max_profiles=per_keyword):
            url = rec.get("profile_url")
            if url and url not in by_url:
                by_url[url] = rec
            if len(by_url) >= max_companies:
                break
        if len(by_url) >= max_companies:
            break
    return list(by_url.values())


def _classify(raw: list[dict]):
    """Hand the scraped records to an Analyst crew for filtering + taxonomy classification.
    Imported lazily so --scrape-only / no-LLM environments don't need crewai installed."""
    from crewai import Crew, Process, Task

    from compute_radar.agents import build_analyst_agent
    from compute_radar.llm_provider import available_providers, build_llm
    from compute_radar.models import StartupList

    providers = available_providers()
    llm = build_llm(providers[0])
    analyst = build_analyst_agent(llm)

    # Compact the scraped records into the Analyst's context.
    listing = "\n".join(
        f"- {r.get('name')} | {r.get('city','?')}, {r.get('country','?')} | "
        f"tags: {r.get('tags','')} | {(r.get('description') or '')[:220]} | "
        f"web: {r.get('website','')} | profile: {r.get('profile_url','')}"
        for r in raw
    )
    task = Task(
        description=(
            "The following companies came from keyword searches of the EU-Startups "
            "directory. Keyword search returns false positives (e.g. a fintech named "
            "'QuantumScale', an EV charger named 'Quantum Charging') - DROP those. Keep only "
            "companies genuinely IN the compute supply chain (semiconductors, quantum/"
            "photonic/neuromorphic hardware, chiplet/packaging, EDA/co-design, power/cooling "
            "for compute, sovereign cloud/edge infrastructure).\n\n"
            "For each kept company produce a Startup record: classify against 1+ taxonomy "
            "layers; set sovereignty_basis from the country (EU/EEA, UK, Switzerland, Norway, "
            "other-Europe, US, Canada, unclear); set funding_type (unclear if not evident); "
            f"set incubator_id to '{INCUBATOR_ID}'; put the profile + website URLs in "
            "source_urls; write an honest architectural_differentiation_note.\n\n"
            f"Companies:\n{listing}"
        ),
        expected_output="A StartupList of only the genuinely compute-relevant companies.",
        agent=analyst,
        output_pydantic=StartupList,
    )
    crew = Crew(agents=[analyst], tasks=[task], process=Process.sequential, verbose=True)
    result = crew.kickoff()
    return result.pydantic.startups if result.pydantic else []


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="Discover compute startups from the EU-Startups directory.")
    ap.add_argument("--keyword", action="append", default=[], help="Keyword to search (repeatable)")
    ap.add_argument("--max", type=int, default=25, help="Max unique companies to classify")
    ap.add_argument("--per-keyword", type=int, default=8, help="Profiles fetched per keyword")
    ap.add_argument("--scrape-only", action="store_true", help="Scrape + print, skip LLM classification")
    args = ap.parse_args()

    keywords = args.keyword or DEFAULT_KEYWORDS
    print(f"Scraping EU-Startups directory for: {keywords}", file=sys.stderr)
    raw = _scrape(keywords, args.max, args.per_keyword)
    print(f"  {len(raw)} unique companies scraped.", file=sys.stderr)

    if args.scrape_only:
        for r in raw:
            print(f"{r.get('name')} | {r.get('city','?')}, {r.get('country','?')} | {(r.get('description') or '')[:100]}")
        return

    try:
        classified = _classify(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"Classification skipped/failed: {exc}", file=sys.stderr)
        return
    print(f"  Analyst kept {len(classified)} genuinely compute-relevant companies.", file=sys.stderr)

    # Merge additively into the dataset.
    from compute_radar.models import RadarSnapshot
    from compute_radar.pipeline import merge

    with open(DATA_PATH, encoding="utf-8") as f:
        snap = RadarSnapshot.model_validate(json.load(f))
    merged = merge(snap.startups, classified)
    out = RadarSnapshot(generated_at=dt.datetime.now(dt.UTC).isoformat(), startups=merged)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        f.write(out.model_dump_json(indent=2))
    print(f"Wrote {len(merged)} total startups to {DATA_PATH}")


if __name__ == "__main__":
    main()
