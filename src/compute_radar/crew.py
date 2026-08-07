"""Assembles and runs the two-agent Crew for a single incubator."""

from __future__ import annotations

from crewai import Crew, Process

from compute_radar.agents import build_analyst_agent, build_scout_agent
from compute_radar.models import StartupList
from compute_radar.tasks import build_analyst_task, build_scout_task


def run_for_incubator(incubator: dict) -> StartupList:
    scout = build_scout_agent()
    analyst = build_analyst_agent()

    scout_task = build_scout_task(scout, incubator)
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
    # Fall back to an empty list rather than crashing the whole pipeline run over
    # one incubator's malformed output - the CLI logs this and moves on.
    return StartupList(startups=[])
