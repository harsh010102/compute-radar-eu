"""News-feed discovery: the third discovery path, alongside the per-incubator Scout and the
EU-Startups directory.

Instead of "who came through program X" (Scout) or "which companies exist under keyword Y"
(directory), this asks "which compute startups were in the news lately" by reading the RSS
feeds of the regional + vertical outlets in config/sources.yaml (see tools/rss_tool.py and
decision.md D2/D32). The deterministic RSS pull + keyword filter produces candidate stories;
the Analyst LLM then extracts the actual startup each story is about, drops non-startup noise
(policy, big-corp, roundups, non-compute), and classifies survivors against the taxonomy.

    python -m compute_radar.discover_from_news                 # last 120 days, all sources
    python -m compute_radar.discover_from_news --since-days 30 # tighter window
    python -m compute_radar.discover_from_news --scrape-only   # print stories, skip the LLM

Results are bucketed under `news-rss` and merged with the same additive merge as the main
pipeline. No-op-safe: if no LLM provider is set it scrapes + reports but skips classification.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from compute_radar.sources import load_sources
from compute_radar.tools.rss_tool import discover_news

ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = ROOT / "data" / "startups.json"
INCUBATOR_ID = "news-rss"


def _classify(stories: list[dict]):
    """Hand scraped news stories to an Analyst crew: extract the startup, filter, classify.
    Imported lazily so --scrape-only / no-LLM environments don't need crewai installed."""
    from crewai import Crew, Process, Task

    from compute_radar.agents import build_analyst_agent
    from compute_radar.llm_provider import build_llm, run_with_provider_fallback
    from compute_radar.models import StartupList

    listing = "\n".join(
        f"- [{s['source']}] {s['title']} ({s.get('published') or 'n.d.'}) | "
        f"{s['summary']} | url: {s['link']}"
        for s in stories
    )
    description = (
            "The following are recent news headlines from European / North-American "
            "startup-news and compute-industry outlets. Each line is one story.\n\n"
            "For each story that is about a SPECIFIC early-stage compute startup - most "
            "usefully a funding round, accelerator cohort, or university spinout - produce a "
            "Startup record for THAT COMPANY (the startup, not the news outlet or a big "
            "incumbent). DROP: policy/regulation news, market roundups, listicles, funds/VCs "
            "themselves, and any company not genuinely in the compute supply chain "
            "(semiconductors, quantum/photonic/neuromorphic hardware, chiplet/packaging, "
            "EDA/co-design, power/cooling for compute, sovereign cloud/edge). A story about a "
            "large established company (Intel, NVIDIA, a hyperscaler) is NOT a find.\n\n"
            "For each kept company: set name to the startup's name; write a one_liner; "
            "classify against 1+ taxonomy layers; set country and sovereignty_basis from the "
            "story (EU/EEA, UK, Switzerland, Norway, other-Europe, US, Canada, unclear); set "
            "funding_type from the story if evident (non_dilutive_grant / convertible_loan / "
            "equity_priced_round / unclear); set funding_eur_m only if an amount is stated; "
            f"set incubator_id to '{INCUBATOR_ID}'; put the story url in source_urls; write an "
            "honest architectural_differentiation_note. Do not invent companies or URLs.\n\n"
            f"Stories:\n{listing}"
    )

    def _run(provider: str):
        analyst = build_analyst_agent(build_llm(provider))
        task = Task(
            description=description,
            expected_output="A StartupList of only the genuinely compute-relevant startups found.",
            agent=analyst,
            output_pydantic=StartupList,
        )
        crew = Crew(agents=[analyst], tasks=[task], process=Process.sequential, verbose=True)
        result = crew.kickoff()
        return result.pydantic.startups if result.pydantic else []

    return run_with_provider_fallback(_run)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="Discover compute startups from startup-news RSS feeds.")
    ap.add_argument("--since-days", type=int, default=120, help="Only stories newer than this")
    ap.add_argument("--max", type=int, default=60, help="Max stories handed to the Analyst")
    ap.add_argument("--scrape-only", action="store_true", help="Scrape + print, skip LLM classification")
    args = ap.parse_args()

    sources = load_sources()
    print(f"Reading {len(sources)} news feeds (last {args.since_days} days)...", file=sys.stderr)
    stories = discover_news(sources, since_days=args.since_days, max_total=args.max)
    print(f"  {len(stories)} compute-relevant stories after keyword filter.", file=sys.stderr)

    if args.scrape_only:
        for s in stories:
            print(f"{s['published'] or 'n.d.'} [{s['source']}] {s['title']} -> {s['link']}")
        return

    if not stories:
        print("Nothing to classify.", file=sys.stderr)
        return

    try:
        classified = _classify(stories)
    except Exception as exc:  # noqa: BLE001 - never let discovery crash a scheduled run
        print(f"Classification skipped/failed: {exc}", file=sys.stderr)
        return
    print(f"  Analyst kept {len(classified)} genuinely compute-relevant startups.", file=sys.stderr)

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
