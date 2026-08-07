"""Pydantic schemas for the pipeline's structured output. These are what the Analyst
agent is asked to fill in, and what the dashboard reads from data/startups.json."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

SovereigntyBasis = Literal["EU/EEA", "UK", "Switzerland", "Norway", "other-Europe", "unclear"]


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
    sovereignty_basis: SovereigntyBasis = "unclear"
    architectural_differentiation_note: str | None = Field(
        default=None,
        description="Why this is (or isn't) a genuine departure from the classical stack, "
        "not just a software layer on existing hardware.",
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
