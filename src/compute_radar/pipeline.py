"""CLI entrypoint. Usage:

    python -m compute_radar.pipeline --all
    python -m compute_radar.pipeline --incubator imec-istart
    python -m compute_radar.pipeline --incubator imec-istart --incubator cea-leti

Merges freshly-scouted startups into data/startups.json, keyed on (name, incubator_id):
a re-run updates existing records in place and adds new ones, but never silently drops a
record that simply wasn't re-found this run (a page can 404 for a day without erasing
history) - it's dropped only if you pass --prune.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from compute_radar.models import RadarSnapshot, Startup

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "incubators.yaml"
DATA_PATH = ROOT / "data" / "startups.json"


def load_incubators() -> list[dict]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_existing_snapshot() -> RadarSnapshot:
    if not DATA_PATH.exists():
        return RadarSnapshot(generated_at=dt.datetime.now(dt.UTC).isoformat(), startups=[])
    with open(DATA_PATH, encoding="utf-8") as f:
        return RadarSnapshot.model_validate(json.load(f))


def _is_empty(v: object) -> bool:
    return v is None or v == "" or v == [] or v == "unclear"


def _merge_record(old: Startup, new: Startup) -> Startup:
    """Field-level merge: fresh data fills gaps but never erases richer existing data.

    A fresh scrape is often thinner than a hand-curated seed record (which may carry
    founders, patents, funding_type, source URLs). So for each field we keep the fresh
    value only when it's non-empty; otherwise we retain what was already there. This keeps
    the weekly auto-run additive — it can enrich or add companies, but a sparse re-scrape
    can't degrade a good record. Lists (layers, founders, source_urls) keep whichever side
    has more entries.
    """
    merged = old.model_copy(deep=True)
    for field in new.model_fields:
        nv = getattr(new, field)
        ov = getattr(merged, field)
        if field in ("layers", "founders", "source_urls"):
            if isinstance(nv, list) and len(nv) > len(ov or []):
                setattr(merged, field, nv)
        elif not _is_empty(nv):
            setattr(merged, field, nv)
    merged.last_verified = dt.date.today()
    return merged


def merge(existing: list[Startup], fresh: list[Startup]) -> list[Startup]:
    by_key = {(s.name.lower(), s.incubator_id): s for s in existing}
    for s in fresh:
        key = (s.name.lower(), s.incubator_id)
        if key in by_key:
            by_key[key] = _merge_record(by_key[key], s)  # gap-fill, don't clobber
        else:
            s.last_verified = dt.date.today()
            by_key[key] = s
    return list(by_key.values())


def main() -> None:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Run the Compute Radar EU scouting pipeline.")
    parser.add_argument("--all", action="store_true", help="Run every incubator in config/incubators.yaml")
    parser.add_argument(
        "--incubator", action="append", default=[], help="Run only this incubator id (repeatable)"
    )
    args = parser.parse_args()

    incubators = load_incubators()
    if args.incubator:
        wanted = set(args.incubator)
        incubators = [i for i in incubators if i["id"] in wanted]
        missing = wanted - {i["id"] for i in incubators}
        if missing:
            print(f"Unknown incubator id(s): {missing}", file=sys.stderr)
            sys.exit(1)
    elif not args.all:
        parser.print_help()
        sys.exit(1)

    # Imported here so `--help` works without an LLM key set.
    from compute_radar.crew import run_for_incubator
    from compute_radar.llm_provider import available_providers

    providers = available_providers()
    print(f"LLM providers available this run: {providers} (round-robin + fallback between them)")

    snapshot = load_existing_snapshot()
    fresh: list[Startup] = []

    for i, incubator in enumerate(incubators):
        provider = providers[i % len(providers)]  # spread load across providers up front,
        # rather than only switching after one is already exhausted
        print(f"\n=== Scouting {incubator['name']} ({incubator['id']}) via {provider} ===")
        try:
            result = run_for_incubator(incubator, provider, providers)
            print(f"  -> found {len(result.startups)} compute-relevant companies")
            fresh.extend(result.startups)
        except Exception as exc:  # noqa: BLE001 - one bad incubator shouldn't kill the run
            print(f"  !! failed: {exc}", file=sys.stderr)

    merged = merge(snapshot.startups, fresh)
    out = RadarSnapshot(generated_at=dt.datetime.now(dt.UTC).isoformat(), startups=merged)

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        f.write(out.model_dump_json(indent=2))

    print(f"\nWrote {len(merged)} total startups to {DATA_PATH}")

    # Best-effort patent-verification pass (deterministic API call, no LLM). No-op unless
    # OPS_CONSUMER_KEY / OPS_CONSUMER_SECRET are configured.
    try:
        from compute_radar.enrich_patents import run_enrichment

        run_enrichment(force=False, founders=False)
    except Exception as exc:  # noqa: BLE001 - enrichment must never fail the whole run
        print(f"  !! patent enrichment skipped: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
