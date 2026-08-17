# Decision Log — Compute Radar EU

Every meaningful design decision in this codebase, with the reasoning and the trade-off
accepted. Grouped by area. Each entry cites where it lives so you can trace decision → code.

> Read this alongside [`flow.md`](./flow.md), which traces how execution actually moves
> through the files these decisions produced.

---

## 1. Product & sourcing strategy

### D1 · Track incubators, not funding databases
**Decision.** The primary signal is *which companies came through an incubator / accelerator
/ fellowship*, not *who just raised a round*.
**Why.** Incubator money (grants, convertible loans, Phase-1/2 non-dilutive support) attaches
to a company **months before** it shows up in Crunchbase or TechCrunch with a priced round.
That lead time *is* the sourcing edge — by the time a deep-tech company is in a VC database,
you're late. This is the whole thesis of the tool.
**Trade-off.** Incubator pages are thin and inconsistent (often just a name + tagline), so we
spend effort on enrichment (news sources, the EU-Startups directory, patents) to compensate.
**Where.** `models.py` (`FundingType` docstring), `taxonomy.py`, `README.md`.

### D2 · Don't scrape Crunchbase — scrape what Crunchbase aggregates from
**Decision.** No Crunchbase dependency. Instead, an `EXTRA` discovery path queries the
regional startup-news outlets and funding tickers that Crunchbase itself ingests.
**Why.** Crunchbase is gated (paid API), JS-heavy, actively anti-bot, and against-ToS to
scrape. But its underlying signal — a Swiss seed round, a French cohort announcement — is
published *first* on regional outlets (Startupticker.ch, Maddyness, ArcticStartup, Silicon
Canals…). Going to the primary source is cheaper, legal, and earlier.
**Trade-off.** We maintain a curated source list per region rather than getting one unified
feed. That list is small on purpose (see D15).
**Where.** `config/sources.yaml`, `src/compute_radar/sources.py`.

### D3 · Fixed 8-layer compute taxonomy, derived from the CDL-NGC thesis
**Decision.** Every company is classified against exactly eight layers, six taken directly
from the CDL Next Gen Computing investment thesis, plus two deliberate edits.
**Why.** A fixed taxonomy makes the dataset filterable and the classification testable
(the Analyst must map to a `key`, not free-text a category). Anchoring it to the actual
thesis means the output speaks the fund's own language.
**The two edits:** `power_thermal` was **split out** of advanced-packaging into its own layer
because a teardown of AI-infra capex showed cooling + power delivery to be a large,
fast-growing cost center in its own right (and one CDL example venture — liquid cooling —
had no clean home otherwise). A company may carry **multiple** layer tags.
**Where.** `src/compute_radar/taxonomy.py`.

---

## 2. LLM pipeline architecture

### D4 · Two agents (Scout + Analyst), not one
**Decision.** A **Scout** agent only reads web pages (tool calls); a separate **Analyst**
agent only reasons/classifies (no tools).
**Why.** It confines the expensive, context-hungry LLM reasoning to a single step and lets
the Scout do as much as possible through plain deterministic tool calls. It also keeps each
agent's prompt small — which matters enormously on free-tier models (see D6).
**Trade-off.** Two LLM roles per incubator instead of one; mitigated because the Scout's
calls are cheap and bounded.
**Where.** `src/compute_radar/agents.py`, `crew.py`.

### D5 · One small task per incubator, not one big prompt
**Decision.** Scout-task and Analyst-task are separate, per-incubator `Task` objects.
**Why.** Free-tier models handle several small, well-scoped prompts far more reliably than
one large one — a big prompt overflows a small context window and silently returns an empty
response.
**Where.** `src/compute_radar/tasks.py`.

### D6 · Free-tier round-robin across OpenRouter **and** Gemini, with fallback
**Decision.** Two separate free LLM services are used together: incubators are spread across
both up front (round-robin), and if the assigned provider fails mid-incubator, the other one
retries that same incubator.
**Why.** Each provider has its own daily free quota (OpenRouter ~50 free-model req/day;
Gemini its own, larger, pool). Splitting the ~29-incubator run across both roughly doubles
how much completes before either quota is exhausted, and gives resilience when one provider
flakes. This is *router-with-two-backends*, not quota-evasion — two legitimately provisioned
services.
**Trade-off.** Two keys to configure; provider-specific quirks to handle. Both are optional —
the tool runs on whichever single key is present.
**Where.** `src/compute_radar/llm_provider.py`, `crew.py::run_for_incubator`.

### D7 · Specific free models: `gpt-oss-20b:free` + `gemini-3.1-flash-lite`
**Decision.** Defaults chosen for **highest free daily quota**, both overridable by env var.
**Why.** The pipeline is a many-call agentic loop, so throughput/quota matters more than raw
quality; the Analyst's job (classify against a fixed taxonomy) is within these models' reach.
Model IDs rotate as providers retire free tiers, so both are env-overridable
(`OPENROUTER_MODEL` / `GEMINI_MODEL`) with the current-model docs linked in code.
**Where.** `src/compute_radar/llm_provider.py`.

### D8 · Fallback triggers on quota-*ish* symptoms, including malformed responses
**Decision.** `looks_like_quota_error()` treats not just clean `429`/`quota` strings but also
the free-tier failure modes — `invalid response from llm call`, and a null-body response that
surfaces as `'NoneType' object is not subscriptable` / `OpenAI API call failed` — as
"provider exhausted, try the other one."
**Why.** A rate-limited free model sometimes returns HTTP 200 with a *null body* instead of a
clean 429; the client then dies indexing `choices[0]`. Without this, that incubator was lost
(observed on `fraunhofer-hhi-silicon-allee`) instead of failing over.
**Trade-off.** A genuine bug might trigger one extra (bounded, non-looping) fallback attempt
before failing loudly — cheap insurance. Genuine bugs (`KeyError`, connection reset) still
re-raise.
**Where.** `src/compute_radar/llm_provider.py::looks_like_quota_error`.

### D9 · Bound the Scout's agentic loop (`max_iter=10`, `respect_context_window`)
**Decision.** Cap the Scout's tool-call iterations and honor the model's context window.
**Why.** A small-context free model degrades badly on long agentic loops — better to return a
partial answer from a bounded number of calls than run until the context silently overflows
into an empty response.
**Where.** `src/compute_radar/agents.py`.

### D10 · Hard 4 000-char cap on scraped page text
**Decision.** Each fetched page is truncated to ~4 000 characters before it reaches the LLM.
**Why.** Free-tier endpoints often carry a much smaller context window than their paid
counterparts, and each task makes multiple tool calls; keeping each cheap stops them
compounding into an overflow that comes back as an empty response.
**Trade-off.** Very long portfolio pages may get clipped; acceptable because the Scout follows
links to individual profiles rather than relying on one giant page.
**Where.** `src/compute_radar/tools/scraper_tool.py`.

### D11 · Malformed structured output → empty result, not a fallback burn
**Decision.** If a crew returns non-conforming output (no `.pydantic`), return an empty
`StartupList` instead of retrying on the other provider.
**Why.** A schema miss is a model-quality problem, not a quota problem — burning a fallback
attempt on it wastes the other provider's budget. Move on to the next incubator.
**Where.** `src/compute_radar/crew.py::_run_once`.

### D12 · One bad incubator never kills the run
**Decision.** The per-incubator loop wraps each run in `try/except`; a failure is logged and
the loop continues.
**Why.** A single 404'd page or provider error shouldn't lose the other 28 incubators' work.
**Where.** `src/compute_radar/pipeline.py::main`.

---

## 3. Scraping & search tooling

### D13 · Plain `requests` + BeautifulSoup, no headless browser
**Decision.** Fetching is `requests.get` + `BeautifulSoup(..., "lxml")`, not Selenium/Playwright.
**Why.** The targets are mostly static institutional pages (incubator portfolios), so a
headless browser is unnecessary weight — and it keeps the whole path free and key-less, which
is what lets the pipeline run in CI for nothing. The swap path (ScrapeWebsiteTool / Playwright)
is documented in-file for the day a JS-rendered portfolio appears.
**Where.** `src/compute_radar/tools/scraper_tool.py`.

### D14 · Exa semantic search (primary) + DuckDuckGo fallback (keyless)
**Decision.** The Scout's web-search tool uses **Exa** neural/auto search when `EXA_API_KEY`
is set, and falls back to scraping **DuckDuckGo's** HTML endpoint when it isn't (or if an Exa
call errors).
**Why.** Exa's semantic search returns far more relevant startup-discovery results than a
scraped SERP, and it's a stable keyed API rather than fragile HTML — but keeping the keyless
DuckDuckGo backend means the pipeline still runs for free with zero configuration. Both
backends return the same `title/url/snippet` text, so agents/tasks are backend-agnostic and
either can be swapped in one file.
**Free-tier discipline.** Exa is used *only while free* and can never generate a paid call:
(1) calls run in Exa's cheapest mode — search only, **no `contents`** retrieval (the Scout
reads pages with its own free scraper); (2) a per-run cap `EXA_MAX_CALLS_PER_RUN` (default
300) ceilings usage; (3) the first `401/402/429` (key rejected / billing / quota) **disables
Exa for the rest of the process** and falls back to DuckDuckGo — no repeated paid hits. Each
query logs which backend served it, so the Actions log confirms Exa is actually in use.
**Trade-off.** Search-only Exa returns no snippets, so the Scout may spend a (free) fetch_page
to judge a result — acceptable, and it shifts cost off the paid API onto our free scraper.
**History.** Originally DuckDuckGo-only; Exa added as primary; then hardened with the
free-tier guard above so a configured key can never bill.
**Where.** `src/compute_radar/tools/search_tool.py` (`_exa_search` / `_ddg_search`,
`_exa_budget_left`, `_exa_state`).

### D15 · Region-matched news sources, kept deliberately short
**Decision.** Each incubator is matched (by country → region) to a handful of relevant
startup-news sites; compute-vertical sources (quantum/semis/HPC/data-center) are always
eligible. Capped at ~6 per incubator.
**Why.** Site-scoped searches against Startupticker/Maddyness/etc. surface funding
announcements the incubator's own site omits — but an unbounded list would blow the Scout's
tool-call budget and the free LLM quota. Short and relevant beats exhaustive.
**Where.** `config/sources.yaml`, `src/compute_radar/sources.py`.

### D16 · A second, deterministic discovery path: the EU-Startups **directory**
**Decision.** Separate from the per-incubator Scout, a keyword-driven scraper hits the
structured EU-Startups *directory* (country/city/description/tags/website per company), then
hands the rich records to the Analyst for filtering + classification.
**Why.** It answers a different question — "which compute companies exist under keyword Y" vs
"who came through program X" — and the directory's structured fields make records
ready-to-classify without a Scout reasoning step. The keyword search is weak (naive substring
match), so the Analyst is the backstop that drops false positives (`Quantum Charging` = EV
charging).
**Where.** `src/compute_radar/discover_from_directory.py`, `tools/directory_tool.py`.

### D17 · Per-host politeness delay + honest User-Agent
**Decision.** `SCRAPE_DELAY_SECONDS` (default 2s) is enforced between requests to the same
host; a descriptive non-commercial UA is sent.
**Why.** Be a good citizen of the free sites we depend on; avoid tripping rate limits.
**Where.** `scraper_tool.py`, `directory_tool.py`, `rss_tool.py`.

### D32 · A third discovery path: RSS news ingester ("read what Crunchbase reads")
**Decision.** A deterministic RSS/Atom ingester reads the regional + compute-vertical
startup-news feeds already listed in `config/sources.yaml`, keyword-filters entries for
compute relevance and recency, and hands the survivors to the same Analyst for
extraction + classification. Bucketed under `news-rss`.
**Why.** This operationalises D2: a funding round or cohort announcement lands on a regional
ticker (Startupticker.ch, Maddyness, ArcticStartup…) *before* it reaches a VC database.
Reading the feeds directly is **more reliable than the Scout's site-scoped SERP search**
(a real feed vs fragile search markup) and **cheaper** (deterministic pull; the LLM only
classifies the pre-filtered shortlist). It reuses the existing sources list, the Analyst, and
the additive merge — so it's a new *input*, not a new *system*.
**feedparser** was chosen for parsing because it tolerates RSS-vs-Atom and messy real-world
date formats that a hand-rolled `lxml` parse would choke on; it's a tiny, pure-Python dep.
Feed URLs resolve by trying `<domain>/feed/`, `www.<domain>/feed/`, `<domain>/rss/`, with an
optional per-source `rss:` override for non-standard paths (e.g. Data Center Dynamics uses
`/en/rss/`).
**Trade-off.** The keyword net is intentionally loose, so the Analyst does more false-positive
filtering; and news discovery finds companies *at* their funding moment (slightly later than
an incubator listing), so it complements rather than replaces the incubator Scout.
**Where.** `src/compute_radar/tools/rss_tool.py`, `src/compute_radar/discover_from_news.py`,
`config/sources.yaml` (optional `rss:` field), wired into `refresh.yml`.

---

## 4. Patent verification (EPO OPS)

### D18 · Official EPO OPS API, not scraping the Deep Tech Finder
**Decision.** Patent signal comes from EPO's official Open Patent Services REST API (OAuth2
client-credentials), not from scraping the EPO Deep Tech Finder frontend.
**Why.** The DTF actively blocks bots; OPS surfaces the same underlying filings **in-ToS** via
a free "non-paying" application. A real EP filing is a strong "differentiation is more than
marketing" signal.
**Where.** `src/compute_radar/tools/patent_tool.py`.

### D19 · Hand-rolled `requests` client, no `python-epo-ops-client`
**Decision.** The OPS client is written directly on `requests`.
**Why.** Full control over the graceful-skip path, token refresh, and throttle handling —
the library would hide exactly the error behavior we most need to control.
**Where.** `patent_tool.py`.

### D20 · Defensive recursive JSON parsing
**Decision.** OPS responses are parsed by recursively searching for keys
(`_find_all`), not against a fixed path.
**Why.** OPS JSON is a 1:1 machine translation of its XML, with `{"$": "..."}` text-nodes and
inconsistent nesting — a brittle fixed path would break constantly.
**Where.** `patent_tool.py::_find_all`, `_extract_pub_numbers`.

### D21 · Patent enrichment is separate from the LLM pipeline
**Decision.** Enrichment is its own module and CLI, run after the crew (and standalone),
enriching `data/startups.json` in place. A no-op (with a hint) if OPS keys are absent.
**Why.** Patent lookup is a deterministic API call, not reasoning — it must not burn LLM
quota or run inside the CrewAI loop. Separating it also lets you backfill the whole dataset
without re-running the expensive crew.
**Where.** `src/compute_radar/enrich_patents.py`; called at the tail of `pipeline.py::main`.

### D22 · Throttle → stop early, don't crash
**Decision.** A `403/429/401` from OPS raises `PatentThrottleError`; callers stop enriching
and save what they have.
**Why.** Never lose the run or the partial enrichment to a transient throttle.
**Where.** `patent_tool.py`, `enrich_patents.py`.

---

## 5. Data, schema & persistence

### D23 · Single `data/startups.json` as the store (Pydantic-validated)
**Decision.** All state is one JSON file, described by `RadarSnapshot`/`Startup` Pydantic
models. No database.
**Why.** It's git-diffable (every weekly refresh is a reviewable commit), the dashboard reads
it directly with a `fetch`, and Pydantic gives schema validation for free. A DB would add
hosting and cost for no benefit at this scale.
**Where.** `src/compute_radar/models.py`, `data/startups.json`.

### D24 · Additive, field-level merge — never clobber, never silently drop
**Decision.** On re-run, records are merged by `(name, incubator_id)`: fresh data fills empty
fields but never overwrites richer existing data; list fields keep whichever side has more
entries; a record not re-found is **kept**, not deleted (dropped only with `--prune`).
**Why.** A weekly auto-scrape is often thinner than a hand-curated seed record (founders,
patents, funding_type). Additive merge means the automation can only *enrich or add* — a
sparse re-scrape can't degrade a good record, and a page 404'ing for a day doesn't erase
history.
**Where.** `src/compute_radar/pipeline.py::_merge_record`, `merge`.

### D25 · `funding_type` is its own field, separate from `stage`
**Decision.** Track the funding *instrument* (non-dilutive grant / convertible / priced
equity / unclear) independently of stage.
**Why.** It's the field that expresses the sourcing edge (D1): the dashboard can filter for
the "grant-only, not yet in VC databases" segment, which is orthogonal to stage (a company
can be seed-stage on a grant alone).
**Where.** `models.py::FundingType`.

### D26 · `team_size` only if stated — never inferred
**Decision.** Team size is set only when a headcount is actually written on a page, left null
otherwise.
**Why.** Inferring team size from funding amount produces confident garbage; a null is more
honest and more useful than a guess.
**Where.** `models.py`, `tasks.py` (Analyst instruction).

### D27 · Sovereignty basis extended EU → +US/Canada as coverage grew
**Decision.** `SovereigntyBasis` started EU/EEA-only (the thesis is European) and gained
US/Canada when incubator coverage expanded to North America (CDL's own network is Canadian).
US/Canada are tracked for market context, **not** EU-sovereignty eligibility.
**Where.** `models.py::SovereigntyBasis`.

---

## 6. Infrastructure & delivery

### D28 · GitHub Actions weekly cron as the "living pipeline"
**Decision.** A scheduled workflow (Mondays ~06:17 UTC) + manual `workflow_dispatch` re-runs
the crew against every incubator and commits the updated JSON.
**Why.** It's free on a public repo — no server to run or pay for — and turns the tool from a
one-shot script into a self-updating dataset. Off-the-hour cron per best practice.
**Where.** `.github/workflows/refresh.yml`.

### D29 · Commit with rebase-onto-concurrent-pushes
**Decision.** The workflow commits, then `git pull --rebase --autostash` before pushing.
**Why.** A dev session (or a second run) can land a commit while a ~1-hour run is in flight; a
plain push would be rejected non-fast-forward and the run's data lost. Rebase-then-push keeps
both.
**Trade-off.** Concurrent edits to the *same* JSON can still conflict; handled manually when
it happens (union merge).
**Where.** `.github/workflows/refresh.yml`.

### D30 · Static single-file dashboard on GitHub Pages
**Decision.** The UI is one `dashboard/index.html` using Leaflet, fetching
`data/startups.json` + `data/incubators.json` at load.
**Why.** Zero-build, zero-cost hosting on GitHub Pages; the data file *is* the API. Markers
are per-incubator (sized by tracked-company count), with client-side filtering.
**Where.** `dashboard/index.html`.

### D31 · `context/` is git-ignored
**Decision.** The `context/` directory (private interview PDFs, working notes) is excluded
from the public repo.
**Why.** The repo is public; personal/case-study material must not be published. Code and
data are public; private working context is not.
**Where.** `.gitignore`.

---

## 7. Open trade-offs / known limitations

- **Search fragility is now mitigated** (D14) — Exa is the primary backend; DuckDuckGo
  remains only as the keyless fallback and is still the weakest link when no Exa key is set.
- **Single-token directory search is weak** (D16) — only distinctive words (`photonic`,
  `quantum`) carry signal; multi-word phrases return nothing. The Analyst compensates.
- **No per-call LLM timeout** — a hung free-tier call can stall one incubator for many
  minutes (observed). A `timeout=` on the crewai `LLM` would bound it, but the kwarg is
  unverified locally (crewai isn't a local dep), so it's deferred to a CI-tested change.
- **Patent enrichment is skipped in CI** unless `OPS_CONSUMER_KEY/SECRET` are set as Actions
  secrets — seed records carry patent data, freshly-scraped ones don't until keys are added.
