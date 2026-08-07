# Compute Radar EU

**A living market map of Europe's compute-focused deep-tech incubators and the startups moving through them.**

**[Live dashboard →](https://harsh010102.github.io/compute-radar-eu/dashboard/)** · [repo](https://github.com/harsh010102/compute-radar-eu) · hosted free on GitHub Pages, data refreshed by the pipeline below.

Compute Radar EU tracks the ~20 European research institutes, university venture labs, and national programs that source and fund next-generation compute startups — semiconductors, quantum, photonics, neuromorphic hardware, chiplet/packaging, sovereign cloud/edge infrastructure, and the power/cooling layer underneath all of it — and keeps a structured, current picture of which startups are moving through each one.

It exists because this information is scattered across ~20 institutional websites, none of which publish a machine-readable feed, and all of which change quarterly (new cohorts, new funding rounds, program restructures). Anyone doing venture sourcing, corporate scouting, or ecosystem analysis in this space is currently doing this by hand, from scratch, every time.

## Why this is a pipeline, not a spreadsheet

A spreadsheet goes stale the day you stop maintaining it. This is a small multi-agent pipeline ([CrewAI](https://github.com/crewAIInc/crewAI)) that re-scrapes each program's public pages, extracts current portfolio/cohort companies, classifies each one against a fixed technology taxonomy, and writes structured output — on a schedule, for free, using an OpenRouter free-tier model for the synthesis step. The dashboard is just a read-only view over that output.

```
config/incubators.yaml   →  the ~20 programs to track (seeded from prior research)
        │
        ▼
   Scout agent          →  fetches each program's site + portfolio/news pages
   (scraper_tool)           free, no paid search API — direct HTTP + BeautifulSoup
        │
        ▼
   Analyst agent         →  extracts company names + classifies each against the
   (OpenRouter LLM)          8-layer compute-stack taxonomy (taxonomy.py)
        │
        ▼
   data/startups.json    →  structured, versioned output (the single source of truth)
        │
        ▼
   dashboard/             →  static HTML/JS market map reading that JSON directly
```

## The taxonomy

Every startup is classified against 8 layers of the compute stack — three from the CDL Next Gen Computing investment thesis (physics/substrate, new architectures, systems/integration, sovereign deployment), refined with a fourth: **power & thermal**, which the sourcing research behind this project found consistently under-represented in written deep-tech theses despite being one of the largest and fastest-growing cost centers in real AI infrastructure builds. See `src/compute_radar/taxonomy.py` for the full definitions and the reasoning behind each layer.

| Layer | What lives here |
|---|---|
| `physics_substrate` | Photonic, quantum, spintronic, cryogenic, compound-semiconductor compute media |
| `new_architecture` | Neuromorphic, memory-centric, RISC-V sovereign silicon |
| `codesign_eda` | Architecture-aware design, EDA tooling, AI-assisted chip design, test & yield |
| `chiplet_interconnect` | Die-to-die interconnects, UCIe, co-packaged optics, in-package memory |
| `advanced_packaging` | 2.5D/3D integration, interposers |
| `power_thermal` | Power delivery, liquid cooling, thermal management for compute |
| `sovereign_cloud` | HPC, sovereign AI factories, attested inference, confidential compute |
| `sovereign_edge_onprem` | Air-gapped deployment, hardware roots of trust, edge inference |

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
pip install -e .       # makes the compute_radar package importable
cp .env.example .env   # add your free OpenRouter API key: https://openrouter.ai/keys
python -m compute_radar.pipeline --all                # full run, all incubators
python -m compute_radar.pipeline --incubator imec-istart   # single program, for testing
```

The dashboard works immediately against the seed data even before you run the pipeline.
Serve it from the **repo root** (not `dashboard/`) so its relative fetch of `../data/*.json` resolves:

```bash
python -m http.server 8080
# open http://localhost:8080/dashboard/
```

## Cost

- **Scraping**: direct HTTP requests + BeautifulSoup against public pages. No paid search API required. A best-effort free web-search fallback (DuckDuckGo HTML) is used only for discovering startups not already listed on a program's own site — see `tools/search_tool.py` for its limits.
- **Synthesis**: OpenRouter, defaulting to a free-tier model (`OPENROUTER_MODEL` in `.env`, currently `openai/gpt-oss-20b:free` — check [openrouter.ai/models](https://openrouter.ai/models?max_price=0) or `curl https://openrouter.ai/api/v1/models` for the current free roster, it rotates and past defaults do get retired without notice). Swap in a paid model for higher accuracy at any time; nothing else in the pipeline changes.
- **Scheduling**: `.github/workflows/refresh.yml` runs the pipeline on a weekly cron via GitHub Actions and commits the updated `data/startups.json` — free on a public repo, no server to maintain.

## Project status

Seed data in `data/startups.json` was hand-researched (see `sources` field on each record) and covers the four CDL-NGC example ventures, ~15 companies found across the incubator network, and the candidates sourced independently for the CDL-NGC cohort task. Everything past that point is designed to be filled in by running the pipeline live.

## Repo layout

```
config/incubators.yaml         the ~20 tracked programs — add a new one by adding a row
src/compute_radar/
  taxonomy.py                  the 8-layer classification schema + definitions
  models.py                    Startup / Incubator data schemas (pydantic)
  tools/scraper_tool.py        CrewAI tool: fetch + clean a URL (requests + BeautifulSoup)
  tools/search_tool.py         CrewAI tool: free DuckDuckGo HTML search (best-effort)
  agents.py                    Scout + Analyst agent definitions
  tasks.py                     the two-step task chain (scout → classify)
  crew.py                      assembles the Crew, runs it per-incubator
  pipeline.py                  CLI entrypoint, writes data/startups.json
data/startups.json             the single source of truth the dashboard reads
data/incubators.json           id -> name/country lookup, hand-derived from incubators.yaml
dashboard/                     static HTML/JS market map (no build step)
.github/workflows/refresh.yml  weekly scheduled re-run
```

## License

MIT — see `LICENSE`.
