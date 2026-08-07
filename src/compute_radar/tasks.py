"""Two-step task chain per incubator: Scout finds companies, Analyst classifies them.
Kept as separate Task objects (rather than one big prompt) so each one stays small enough
for a free-tier model to handle reliably."""

from __future__ import annotations

from crewai import Agent, Task

from compute_radar.models import StartupList


def build_scout_task(agent: Agent, incubator: dict) -> Task:
    portfolio_url = incubator.get("portfolio_url") or incubator["url"]
    return Task(
        description=(
            f"Research the incubator '{incubator['name']}' ({incubator['country']}).\n"
            f"Start at {portfolio_url} using fetch_page. Follow links that look like "
            "individual startup/portfolio/cohort pages. If the page doesn't list current "
            "companies, use free_web_search with a query like "
            f"'{incubator[\"name\"]} 2026 cohort portfolio companies' as a fallback.\n\n"
            "List every current or recent (last ~18 months) compute-relevant company you "
            "find: name, one-line description, country if stated, funding/stage if stated, "
            "and the exact URL you found it on. Do not invent companies or URLs - if you "
            "can't find any, say so explicitly rather than guessing."
        ),
        expected_output=(
            "A plain-text list of companies found, each with: name, description, "
            "country, stage/funding if known, source URL. Or an explicit statement that "
            "none were found, with what was tried."
        ),
        agent=agent,
    )


def build_analyst_task(agent: Agent, incubator: dict, scout_task: Task) -> Task:
    return Task(
        description=(
            f"Using the Scout's findings about '{incubator['name']}' (incubator id: "
            f"'{incubator['id']}', country: '{incubator['country']}'), produce a "
            "structured record for every company that is genuinely compute-relevant "
            "(semiconductors, quantum, photonics, HPC, AI hardware, chiplet/packaging, "
            "power/cooling for compute, sovereign cloud/edge infrastructure). Skip "
            "companies that are off-thesis (e.g. pure software/SaaS with no hardware or "
            "infrastructure angle).\n\n"
            "For each company: classify against 1+ taxonomy layers (use the layer `key` "
            "values exactly as given in your backstory), assess sovereignty_basis "
            "(EU/EEA, UK, Switzerland, Norway, other-Europe, or unclear - based on where "
            "it's headquartered/incorporated, not where its founders are from), and write "
            "a short, honest architectural_differentiation_note - including saying "
            "plainly if a company is mostly a software/tooling layer rather than a new "
            "physical or architectural approach. Set incubator_id to "
            f"'{incubator['id']}'. Carry over every source URL the Scout found."
        ),
        expected_output=(
            "A StartupList: one Startup record per genuinely compute-relevant company "
            "found, fully populated per the schema."
        ),
        agent=agent,
        context=[scout_task],
        output_pydantic=StartupList,
    )
