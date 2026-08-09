"""Deterministic patent-enrichment pass over data/startups.json using EPO OPS.

Separate from the LLM pipeline on purpose: patent lookup is a plain API call, not a
reasoning task, so it should not burn LLM quota or run inside the CrewAI loop. Run it
standalone to backfill the whole dataset:

    python -m compute_radar.enrich_patents            # only companies not yet checked
    python -m compute_radar.enrich_patents --force    # re-check everything
    python -m compute_radar.enrich_patents --founders # also look up founders as inventors

pipeline.py also calls run_enrichment() at the end of a full run when OPS is configured,
so a scheduled refresh keeps the patent signal current automatically.

Needs OPS_CONSUMER_KEY / OPS_CONSUMER_SECRET (free, https://developers.epo.org). Without
them this is a no-op that prints a hint and leaves the data untouched.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from compute_radar.tools.patent_tool import (
    PatentThrottleError,
    is_configured,
    lookup_applicant_patents,
    lookup_inventor_patents,
)

ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = ROOT / "data" / "startups.json"


def run_enrichment(force: bool = False, founders: bool = False, verbose: bool = True) -> int:
    """Enrich data/startups.json in place. Returns the number of records updated."""
    if not is_configured():
        if verbose:
            print(
                "OPS not configured (set OPS_CONSUMER_KEY / OPS_CONSUMER_SECRET from "
                "https://developers.epo.org) - skipping patent enrichment.",
                file=sys.stderr,
            )
        return 0
    if not DATA_PATH.exists():
        print(f"No data file at {DATA_PATH}", file=sys.stderr)
        return 0

    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    today = dt.date.today().isoformat()
    updated = 0
    try:
        for s in data.get("startups", []):
            if s.get("patents") and not force:
                continue
            name = s["name"]
            try:
                info = lookup_applicant_patents(name)
            except PatentThrottleError as exc:
                print(f"  ! stopping early: {exc}", file=sys.stderr)
                break
            if info is None:
                continue

            # Optional: also try founders as inventors, merging counts/samples.
            if founders and info["patent_count"] == 0:
                for fdr in s.get("founders", []):
                    try:
                        fi = lookup_inventor_patents(fdr["name"])
                    except PatentThrottleError as exc:
                        print(f"  ! stopping early: {exc}", file=sys.stderr)
                        fi = None
                        break
                    if fi and fi["patent_count"] > 0:
                        info["patent_count"] += fi["patent_count"]
                        info["samples"] = (info["samples"] + fi["samples"])[:6]
                        info["has_ep_patents"] = info["has_ep_patents"] or fi["has_ep_patents"]
                        info["query_name"] = f'{name} (+inventor {fdr["name"]})'

            info["checked_at"] = today
            s["patents"] = info
            updated += 1
            if verbose:
                print(f"  {name}: {info['patent_count']} filing(s) {info['samples'][:3]}")
    finally:
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    if verbose:
        print(f"Patent-enriched {updated} record(s).")
    return updated


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="EPO OPS patent enrichment for Compute Radar.")
    ap.add_argument("--force", action="store_true", help="Re-check records already enriched")
    ap.add_argument("--founders", action="store_true", help="Also look up founders as inventors")
    args = ap.parse_args()
    run_enrichment(force=args.force, founders=args.founders)


if __name__ == "__main__":
    main()
