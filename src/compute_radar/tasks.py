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
            f"'{incubator['name']} 2026 cohort portfolio companies' as a fallback.\n\n"
            "List every current or recent (last ~18 months) compute-relevant company you "
            "find: name, one-line description, country if stated, funding/stage if stated, "
            "team size if stated, founder name(s)/role(s) if the page names them, and the "
            "exact URL you found it on. If a founder's LinkedIn, GitHub, or research profile "
            "(Google Scholar, ORCID, ResearchGate, faculty page) happens to be linked on the "
            "page you're already reading, capture that URL too - but do not spend extra "
            "tool calls specifically hunting for founder social profiles, that's out of "
            "budget for this task. Do not invent companies, people, or URLs - if you can't "
            "find any, say so explicitly rather than guessing."
        ),
        expected_output=(
            "A plain-text list of companies found, each with: name, description, "
            "country, stage/funding if known, team size if known, founders (name/role/any "
            "links found) if known, source URL. Or an explicit statement that none were "
            "found, with what was tried."
        ),
        agent=agent,
    )


def build_analyst_task(agent: Agent, incubator: dict, scout_task: Task) -> Task:
    return Task(
        description=(
            f"Using the Scout's findings about '{incubator['name']}' (incubator id: "
            f"'{incubator['id']}', country: '{incubator['country']}'), produce a "
            "structured record for every company that is genuinely compute-relevant.\n\n"
            "COMPUTE-RELEVANT means the company's core product changes how compute itself "
            "is built, delivered, or powered: semiconductors, quantum/photonic/neuromorphic "
            "hardware, chiplet/packaging, EDA/co-design tooling, power delivery or cooling "
            "for compute infrastructure, or sovereign cloud/edge compute infrastructure.\n\n"
            "NOT compute-relevant, even though it's hardware and even though it contains a "
            "chip: a drone, medical device, robot, or implant that merely embeds an "
            "off-the-shelf processor to do something else (agriculture, healthcare, "
            "logistics, etc). The test is whether the company is IN the compute-supply-chain, "
            "not whether it USES compute. If you catch yourself writing a "
            "differentiation note that says a company 'does not introduce a new compute "
            "architecture or substrate' or similar - that is a signal to DROP it from your "
            "output entirely, not to include it with a layer tag anyway. A company that "
            "fails your own reasoning must not appear in the final list.\n\n"
            "For each company that survives that filter: classify against 1+ taxonomy "
            "layers (use the layer `key` values exactly as given in your backstory), assess "
            "sovereignty_basis (EU/EEA, UK, Switzerland, Norway, other-Europe, or unclear - "
            "based on where it's headquartered/incorporated, not where its founders are "
            "from), assess funding_type (non_dilutive_grant if the money is a grant/prize "
            "with no equity taken; convertible_loan if it's debt that may convert later; "
            "equity_priced_round if it's a normal priced VC round with equity changing "
            "hands; unclear if not stated), and set team_size_estimate ONLY if a headcount "
            "was actually stated somewhere (e.g. '~300 employees') - leave it null rather "
            "than inferring it from funding size.\n\n"
            "Both grant-funded AND equity-funded companies are valid finds here - the goal "
            "is EARLY-STAGE companies specifically (small founding teams, not established "
            "50-200+ person companies), regardless of which funding instrument they used. A "
            "company on a big priced round is still worth including if it's genuinely small "
            "and early; a company with only a grant is still worth flagging as more mature "
            "if you find evidence of a large team. Say so honestly in "
            "architectural_differentiation_note when a company looks more mature than its "
            "funding amount alone would suggest.\n\n"
            "Carry over any founders the Scout found (name, role, and any LinkedIn/GitHub/"
            "research-profile URL it happened to capture) into the founders field - do not "
            "fabricate a profile URL that wasn't actually found. Set incubator_id to "
            f"'{incubator['id']}'. Carry over every source URL the Scout found."
        ),
        expected_output=(
            "A StartupList: one Startup record per genuinely compute-relevant company "
            "that survives the filter above, fully populated per the schema. Companies "
            "that don't survive the filter must not appear at all."
        ),
        agent=agent,
        context=[scout_task],
        output_pydantic=StartupList,
    )
