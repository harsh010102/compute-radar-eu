"""Pydantic schemas for the pipeline's structured output. These are what the Analyst
agent is asked to fill in, and what the dashboard reads from data/startups.json."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# Jurisdiction where the company is incorporated/HQ'd. Started Europe-only (the sovereignty
# thesis is European); extended to US/Canada when the pipeline's incubator coverage grew to
# North America (CDL's own network is Canadian). For a European-sovereignty venture the
# EU/EEA value is the "can access EU Chips Act / EIC / EuroHPC" signal; US/Canada are tracked
# for market context and CDL-network alignment, not EU-sovereignty eligibility.
SovereigntyBasis = Literal[
    "EU/EEA", "UK", "Switzerland", "Norway", "other-Europe", "US", "Canada", "unclear"
]

# The whole point of tracking incubators rather than funding databases: incubator money
# (grants, convertible loans, Phase 1/2 non-dilutive support) surfaces companies BEFORE
# they show up in Crunchbase/TechCrunch with a priced round - that's the actual sourcing
# edge. This field lets the dashboard filter for exactly that "not yet discovered by VC
# databases" segment, separate from stage (a company can be "seed" stage on a grant only).
FundingType = Literal[
    "non_dilutive_grant",  # pure grant/prize money, no equity taken (e.g. ChipStart EU, MAXimize Phase 1)
    "convertible_loan",  # imec.istart-style - debt now, may convert to equity later
    "equity_priced_round",  # a normal VC round (seed/Series A/etc.) - already "discovered"
    "unclear",
]


class PatentInfo(BaseModel):
    """EPO Open Patent Services verification signal — see tools/patent_tool.py.
    A real EP filing is a strong 'differentiation is more than marketing' indicator."""

    query_name: str
    patent_count: int = 0
    has_ep_patents: bool = False
    samples: list[str] = Field(default_factory=list, description="Sample publication numbers, e.g. EP4012345A1")
    checked_at: date | None = None
    source: str = "EPO OPS"


class Founder(BaseModel):
    name: str
    role: str | None = Field(default=None, description="e.g. CEO, CTO, Co-founder")
    linkedin_url: str | None = None
    github_url: str | None = None
    research_profile_url: str | None = Field(
        default=None,
        description="Google Scholar, ORCID, ResearchGate, or an institutional faculty page - "
        "whichever actually exists for this person.",
    )
    note: str | None = Field(
        default=None,
        description="One line on research/technical pedigree if findable, e.g. "
        "'PhD Photonics, ICFO; 15+ papers on quantum RNG'. Leave null rather than guess.",
    )


class Startup(BaseModel):
    name: str
    one_liner: str = Field(description="One sentence: what they build.")
    incubator_id: str = Field(description="Matches an id in config/incubators.yaml")
    country: str
    layers: list[str] = Field(
        description="One or more keys from compute_radar.taxonomy.TAXONOMY", default_factory=list
    )
    stage: str | None = Field(
        default=None, description="e.g. pre-seed, seed, Series A, commercial"
    )
    funding_eur_m: float | None = Field(
        default=None, description="Total disclosed funding in EUR millions, if known"
    )
    funding_type: FundingType = "unclear"
    team_size_estimate: str | None = Field(
        default=None,
        description="Free text, only if actually stated somewhere (e.g. '~300 employees', "
        "'5-person founding team') - not a guess from funding size alone.",
    )
    sovereignty_basis: SovereigntyBasis = "unclear"
    architectural_differentiation_note: str | None = Field(
        default=None,
        description="Why this is (or isn't) a genuine departure from the classical stack, "
        "not just a software layer on existing hardware.",
    )
    founders: list[Founder] = Field(default_factory=list)
    patents: PatentInfo | None = Field(
        default=None, description="EPO OPS patent-verification signal; null until enriched."
    )
    source_urls: list[str] = Field(default_factory=list)
    last_verified: date | None = None


class Incubator(BaseModel):
    id: str
    name: str
    country: str
    compute_focus: Literal["high", "medium"]
    eligibility: Literal["internal", "external", "hybrid"]
    url: str
    portfolio_url: str | None = None
    notes: str | None = None


class StartupList(BaseModel):
    """Wrapper so the Analyst task can use CrewAI's output_pydantic (expects one model,
    not a bare list)."""

    startups: list[Startup]


class RadarSnapshot(BaseModel):
    """The full contents of data/startups.json."""

    generated_at: str
    startups: list[Startup]
