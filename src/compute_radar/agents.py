"""Agent definitions. Two agents, deliberately: a Scout that only reads pages and a
separate Analyst that only reasons/classifies. Splitting them (rather than one agent doing
both) keeps the expensive LLM calls confined to the reasoning step and lets the Scout do as
much of its work as possible via plain tool calls."""

from __future__ import annotations

import os

from crewai import LLM, Agent

from compute_radar.taxonomy import describe_taxonomy_for_prompt
from compute_radar.tools.scraper_tool import FetchPageTool
from compute_radar.tools.search_tool import FreeWebSearchTool


def get_llm() -> LLM:
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-20b:free")
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add a free key "
            "from https://openrouter.ai/keys"
        )
    return LLM(
        model=f"openrouter/{model}",
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.2,
    )


def build_scout_agent() -> Agent:
    return Agent(
        role="Incubator Scout",
        goal=(
            "Given one European deep-tech incubator, find every current or recent "
            "(last ~18 months) compute-relevant portfolio, cohort, or spinoff company it "
            "has produced, with a one-line description and a source URL for each."
        ),
        backstory=(
            "You research European deep-tech incubators for a living. You are careful to "
            "distinguish a company that is actually in the program's current or recent "
            "cohort from one that is merely mentioned in passing (e.g. as a partner or "
            "sponsor). You always cite the URL you found each company on."
        ),
        tools=[FetchPageTool(), FreeWebSearchTool()],
        llm=get_llm(),
        verbose=True,
        allow_delegation=False,
        max_iter=10,  # bound the tool-call loop - a free-tier model with a small context
        # window degrades badly on long agentic loops; better to return a partial answer
        # from a bounded number of calls than run until the context silently overflows.
        # 6 proved crash-safe but under-covers a large portfolio page; 10 is the next step
        # up now that per-call output is capped small (see scraper_tool.py).
        respect_context_window=True,
    )


def build_analyst_agent() -> Agent:
    return Agent(
        role="Compute Stack Analyst",
        goal=(
            "Classify each startup the Scout found against the fixed 8-layer compute-stack "
            "taxonomy, and assess its architectural differentiation and sovereignty basis "
            "honestly - including saying so when a company doesn't clearly fit any layer, "
            "rather than forcing a fit."
        ),
        backstory=(
            "You are a skeptical technical analyst, not a hype writer. You have read the "
            "CDL Next Gen Computing investment thesis and understand the difference between "
            "a genuine architectural departure (new physics, new architecture) and a "
            "software/tooling layer riding on existing hardware - and you say which one "
            "you're looking at. Taxonomy you must classify against:\n\n"
            f"{describe_taxonomy_for_prompt()}"
        ),
        tools=[],
        llm=get_llm(),
        verbose=True,
        allow_delegation=False,
        respect_context_window=True,
    )
