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


def merge(existing: list[Startup], fresh: list[Startup]) -> list[Startup]:
    by_key = {(s.name.lower(), s.incubator_id): s for s in existing}
    for s in fresh:
        s.last_verified = dt.date.today()
        by_key[(s.name.lower(), s.incubator_id)] = s
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

    # Imported here so `--help` works without OPENROUTER_API_KEY set.
    from compute_radar.crew import run_for_incubator

    snapshot = load_existing_snapshot()
    fresh: list[Startup] = []

    for incubator in incubators:
        print(f"\n=== Scouting {incubator['name']} ({incubator['id']}) ===")
        try:
            result = run_for_incubator(incubator)
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


if __name__ == "__main__":
    main()
