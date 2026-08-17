# Execution Flow — Compute Radar EU

How execution travels through the codebase: the entry points, the order things run in, and
what calls what. Read with [`decision.md`](./decision.md) for *why* each piece is shaped the
way it is.

---

## 1. The four entry points

| # | Entry point | Command | What it does |
|---|-------------|---------|--------------|
| 1 | **Weekly CI** | `.github/workflows/refresh.yml` | Runs #2, #3 and #4 on a schedule, commits the data. The main way the dataset stays alive. |
| 2 | **Main pipeline** | `python -m compute_radar.pipeline --all` | Per-incubator Scout→Analyst crew → merge → write → patent-enrich. |
| 3 | **Directory discovery** | `python -m compute_radar.discover_from_directory` | Keyword scrape of the EU-Startups directory → Analyst classify → merge. |
| 4 | **News-RSS discovery** | `python -m compute_radar.discover_from_news` | RSS pull of the regional/vertical news feeds → keyword filter → Analyst classify → merge. |
| 5 | **Patent enrichment** | `python -m compute_radar.enrich_patents` | Deterministic EPO OPS backfill over the existing dataset. |
| — | **Dashboard** (read-only) | `dashboard/index.html` (GitHub Pages) | Fetches the produced JSON and renders the map. Consumes, never writes. |

All four Python entries call `load_dotenv()` first so `.env` (or CI secrets) populate the
API keys before anything runs.

---

## 2. Primary flow — `pipeline.py --all`

This is the spine of the tool. Entry: `src/compute_radar/pipeline.py::main`.

```
main()                                             # pipeline.py
│
├─ load_dotenv(.env)                               # API keys into env
├─ parse args (--all / --incubator id ...)
│
├─ load_incubators()            ── reads config/incubators.yaml → list[dict]
├─ load_existing_snapshot()     ── reads data/startups.json → RadarSnapshot   (D24: never lost)
├─ available_providers()        ── llm_provider.py: which of openrouter/gemini have keys
│
└─ for i, incubator in enumerate(incubators):
      provider = providers[i % len(providers)]     # round-robin up front              (D6)
      │
      └─ run_for_incubator(incubator, provider, providers)      # crew.py
         │
         ├─ _run_once(incubator, provider)
         │    ├─ build_llm(provider)                            # llm_provider.py       (D7)
         │    ├─ build_scout_agent(llm)                         # agents.py             (D4)
         │    ├─ build_analyst_agent(llm)                       # agents.py
         │    │      └─ describe_taxonomy_for_prompt()          # taxonomy.py           (D3)
         │    ├─ sources_for(incubator)  +  format_sources_for_prompt()  # sources.py   (D15)
         │    │      └─ region_for(country) → load_sources() → config/sources.yaml
         │    ├─ build_scout_task(scout, incubator, sources_line)   # tasks.py          (D5)
         │    ├─ build_analyst_task(analyst, incubator, scout_task) # tasks.py
         │    └─ Crew(sequential).kickoff()
         │         ├─ SCOUT runs → uses tools:
         │         │     • FetchPageTool._run   → requests+BS4         # tools/scraper_tool.py (D13)
         │         │     • FreeWebSearchTool._run → Exa (or DDG fallback) # tools/search_tool.py (D14)
         │         │   → plain-text list of companies + source URLs
         │         └─ ANALYST runs (context = scout output, no tools)
         │               → filters non-compute, classifies vs taxonomy
         │               → output_pydantic = StartupList          # models.py
         │
         └─ on Exception:                                        # crew.py
              looks_like_quota_error(exc)?                                              (D8)
                ├─ yes + other provider exists → _run_once(incubator, fallback)         (D6)
                └─ no  → re-raise  → caught per-incubator in main(), logged, loop continues (D12)
      │
      └─ fresh.extend(result.startups)
│
├─ merge(snapshot.startups, fresh)   ── field-level additive merge, key=(name,incubator_id)  (D24)
│      └─ _merge_record(old, new)    ── fill gaps, keep richer, keep longer lists
├─ write data/startups.json          ── RadarSnapshot.model_dump_json(indent=2)
│
└─ run_enrichment(force=False)       ── enrich_patents.py; no-op if OPS keys absent      (D21)
```

**Order in one line:** config + existing data → per-incubator (round-robin provider →
Scout reads → Analyst classifies → fallback if quota) → additive merge → write JSON →
patent-enrich.

### The Scout ⇄ Analyst hand-off (the heart of it)
- **Scout** (`build_scout_agent`) has two tools and one job: read the incubator's
  portfolio/cohort page, follow profile links, optionally site-search the regional news
  sources, and emit a **plain-text** list of companies with source URLs. Bounded to
  `max_iter=10` (D9). No classification — it doesn't reason about the taxonomy.
- **Analyst** (`build_analyst_agent`) has **no tools**. It receives the Scout's text as
  `context`, drops anything not genuinely in the compute supply chain, and emits a
  `StartupList` of `Startup` records classified against the 8 taxonomy layers, with
  sovereignty/funding/founder fields filled per the schema.

---

## 3. Directory-discovery flow — `discover_from_directory.py`

A *separate* discovery path (D16). No Scout; the directory itself is structured, so a
deterministic scrape feeds the Analyst directly.

```
main()                                             # discover_from_directory.py
│
├─ _scrape(keywords, max, per_keyword)             # default kws: photonic, quantum, silicon, …
│    └─ for kw: directory_tool.discover(kw)        # tools/directory_tool.py
│         ├─ search_directory(kw)   → [{name, profile_url}]   (EU-Startups /directory search)
│         └─ fetch_listing(url)     → {country, city, description, tags, funding, website}
│    → dedup by profile_url → rich records
│
├─ (--scrape-only?) print and stop
│
└─ _classify(raw)                                  # LLM step
     ├─ build_analyst_agent(build_llm(providers[0]))   # agents.py — same Analyst as pipeline
     ├─ Crew(sequential).kickoff() with a single classify Task (output_pydantic=StartupList)
     │     → drops false positives (e.g. "Quantum Charging"=EV), classifies survivors
     └─ merge(snapshot, classified)  → write data/startups.json     # reuses pipeline.merge (D24)
```

Bucketed under `incubator_id = "eu-startups-directory"`, merged with the same additive
merge as the main pipeline so the two paths compose without clobbering each other.

---

## 4. News-RSS discovery flow — `discover_from_news.py`

The third discovery path (D32). No Scout; the feeds are structured, so a deterministic pull
feeds the Analyst directly — the "read what Crunchbase reads" path.

```
main()                                             # discover_from_news.py
│
├─ load_sources()                                  # sources.py → config/sources.yaml (all feeds)
├─ discover_news(sources, since_days, max_total)   # tools/rss_tool.py
│    └─ for src: fetch_feed(src)
│         ├─ _candidate_urls(src)  → rss override, else /feed/, www./feed/, /rss/
│         ├─ requests.get (polite, UA) → feedparser.parse   → entries
│         └─ per entry: recency cutoff + _is_compute(keywords) filter
│    → [{title, summary, link, source, published}]  (deduped by link)
│
├─ (--scrape-only?) print stories and stop
│
└─ _classify(stories)                              # LLM step
     ├─ build_analyst_agent(build_llm(providers[0]))    # agents.py — same Analyst
     ├─ Crew(sequential).kickoff() with one Task (output_pydantic=StartupList)
     │     → extract the STARTUP each story is about; drop policy/roundups/big-corp/non-compute
     └─ merge(snapshot, classified) → write data/startups.json   # reuses pipeline.merge (D24)
```

Bucketed under `incubator_id = "news-rss"`; additive-merged so it composes with the incubator
and directory paths without clobbering.

---

## 5. Patent-enrichment flow — `enrich_patents.py`

Deterministic, LLM-free (D21). Runs standalone or as the tail of the main pipeline.

```
run_enrichment(force, founders)                    # enrich_patents.py
│
├─ is_configured()? ── no → print hint, return 0 (graceful no-op)   (D21)
├─ load data/startups.json
│
└─ for each startup (skip if already has patents unless --force):
     ├─ lookup_applicant_patents(name)             # tools/patent_tool.py
     │    ├─ _get_token()   → OAuth2 client-credentials, cached w/ expiry   (D18)
     │    ├─ _polite()      → spacing between OPS calls
     │    ├─ GET published-data/search?q=pa="<name>"
     │    ├─ 403/429/401 → raise PatentThrottleError → caller stops early    (D22)
     │    └─ parse defensively: _find_all() + _extract_pub_numbers()         (D20)
     ├─ (--founders and 0 hits?) lookup_inventor_patents(founder) and merge counts
     └─ write s["patents"] = PatentInfo-shaped dict
│
└─ save data/startups.json in place (finally-block: partial progress always saved)
```

---

## 6. CI flow — `.github/workflows/refresh.yml`

```
on: schedule (Mon 06:17 UTC) | workflow_dispatch(incubator?)        (D28)
│
job refresh (ubuntu-latest):
  1. checkout
  2. setup-python 3.12
  3. pip install -r requirements.txt ; pip install -e .
  4. Run pipeline:
       INCUBATOR_INPUT set → python -m compute_radar.pipeline --incubator <id>
       else               → python -m compute_radar.pipeline --all
       (env: OPENROUTER_API_KEY, GEMINI_API_KEY, EXA_API_KEY, OPS_* , model overrides)
  5. Discover from EU-Startups directory   (full run only; `|| true` — never fails job)
  6. Discover from news feeds (RSS)        (full run only; `|| true` — never fails job)  (D32)
  7. Commit updated data:
       git add data/startups.json
       diff --cached --quiet → "no data changes", exit 0
       else commit → git pull --rebase --autostash origin main → push      (D29)
```

Patent enrichment happens inside step 4 (pipeline tail) when `OPS_*` secrets are present.

---

## 7. Dashboard flow — `dashboard/index.html`

Read-only consumer, hosted on GitHub Pages (D30).

```
DOMContentLoaded
├─ fetch("../data/startups.json")  +  fetch("../data/incubators.json")
├─ L.map(...) with a tile layer                       # Leaflet from unpkg CDN
├─ drawMarkers():
│    for each incubator: L.circleMarker([lat,lon]) sized by #tracked companies
│    marker.bindPopup(list of that incubator's startups)
└─ client-side filters (layer, funding_type, sovereignty) re-run drawMarkers()
```

The dashboard never writes — it only renders whatever the pipeline last committed.

---

## 8. File-responsibility map

| File | Responsibility |
|------|----------------|
| `pipeline.py` | **Entry** + orchestration: load config/data, per-incubator loop, merge, write, trigger enrichment. |
| `crew.py` | Assemble the Scout+Analyst Crew for one incubator; run it; provider fallback logic. |
| `agents.py` | Define the two agents (Scout with tools, Analyst tool-less + taxonomy in backstory). |
| `tasks.py` | The two per-incubator task prompts (Scout: find; Analyst: filter + classify). |
| `llm_provider.py` | Which provider(s) are configured; build the `LLM`; round-robin/fallback helpers; quota-error sniffing. |
| `taxonomy.py` | The fixed 8-layer taxonomy + prompt rendering. |
| `models.py` | Pydantic schema: `Startup`, `StartupList`, `RadarSnapshot`, `Founder`, `PatentInfo`, enums. |
| `sources.py` | Match an incubator → region → relevant news sources for the Scout. |
| `discover_from_directory.py` | **Entry** for the keyword/directory discovery path. |
| `discover_from_news.py` | **Entry** for the news-RSS discovery path. |
| `enrich_patents.py` | **Entry** for deterministic EPO OPS patent enrichment. |
| `tools/scraper_tool.py` | `FetchPageTool` — polite requests+BS4 page fetch. |
| `tools/search_tool.py` | `FreeWebSearchTool` — Exa search (cheapest mode, per-run cap, auto-disable on 401/402/429) with DuckDuckGo fallback. |
| `tools/directory_tool.py` | EU-Startups directory scraper (`search_directory`, `fetch_listing`, `discover`). |
| `tools/rss_tool.py` | RSS/Atom feed fetch + compute keyword filter for the news path (`discover_news`). |
| `tools/patent_tool.py` | EPO OPS client (auth, search, defensive parse, throttle handling). |
| `config/incubators.yaml` | The ~29 tracked incubators (id, name, country, urls). |
| `config/sources.yaml` | Regional + compute-vertical news sources for the Scout. |
| `.github/workflows/refresh.yml` | Weekly cron that runs the pipeline and commits data. |
| `dashboard/index.html` | Leaflet map that reads the produced JSON. |
| `data/startups.json` | The single source of truth the whole tool produces and the dashboard consumes. |

---

## 9. Data lifecycle in one picture

```
config/incubators.yaml ─┐        config/sources.yaml ──────────┬───────────────┐
                        ▼                                       ▼               ▼
        ┌─── Scout (read web) ────┐          directory_tool (EU-Startups)   rss_tool (news feeds)
        │  scraper_tool · search   │          keyword scrape                keyword-filtered pull
        │           ▼              │                 │                            │
        │  Analyst (classify)      │             Analyst                      Analyst
        └───────────┬─────────────┘                 │                            │
                    └───────────────┬───────────────┴────────────────────────────┘
                                    ▼
                          merge()  ◄── additive, non-destructive  (key: name + incubator_id)
                                    ▼
            data/startups.json ──► enrich_patents (EPO OPS) ──► data/startups.json
                                    ▼
            dashboard/index.html  (Leaflet map on GitHub Pages)
```
