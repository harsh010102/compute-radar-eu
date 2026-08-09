"""Assembles and runs the two-agent Crew for a single incubator, with the
OpenRouter<->Gemini round-robin + fallback described in llm_provider.py."""

from __future__ import annotations

import sys

from crewai import Crew, Process

from compute_radar.agents import build_analyst_agent, build_scout_agent
from compute_radar.llm_provider import build_llm, looks_like_quota_error, other_provider
from compute_radar.models import StartupList
from compute_radar.sources import format_sources_for_prompt, sources_for
from compute_radar.tasks import build_analyst_task, build_scout_task


def _run_once(incubator: dict, provider: str) -> StartupList:
    llm = build_llm(provider)
    scout = build_scout_agent(llm)
    analyst = build_analyst_agent(llm)

    sources_line = format_sources_for_prompt(sources_for(incubator))
    scout_task = build_scout_task(scout, incubator, sources_line)
    analyst_task = build_analyst_task(analyst, incubator, scout_task)

    crew = Crew(
        agents=[scout, analyst],
        tasks=[scout_task, analyst_task],
        process=Process.sequential,
        verbose=True,
    )

    result = crew.kickoff()

    if result.pydantic is not None:
        return result.pydantic
    # Malformed structured output, not a provider failure - don't burn a fallback attempt
    # on it, just return empty and let the caller move to the next incubator.
    return StartupList(startups=[])


def run_for_incubator(incubator: dict, provider: str, providers: list[str]) -> StartupList:
    """Try `provider` first (the one this incubator was round-robin-assigned to). If it
    fails with something that looks like a quota/rate-limit problem and another provider
    is configured, retry the same incubator once on that other provider before giving up."""
    try:
        return _run_once(incubator, provider)
    except Exception as exc:  # noqa: BLE001 - decide fallback-vs-reraise below
        if not looks_like_quota_error(exc):
            raise
        fallback = other_provider(provider, providers)
        if fallback is None:
            raise
        print(
            f"  !! {provider} looked exhausted ({exc}); retrying on {fallback}",
            file=sys.stderr,
        )
        return _run_once(incubator, fallback)
