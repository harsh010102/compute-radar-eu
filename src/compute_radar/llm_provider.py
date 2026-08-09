"""Picks which LLM backend a given run uses, and alternates between them.

Two free tiers, two different daily quotas (OpenRouter: 50 free-model requests/day per
account; Gemini: its own separate quota). Splitting the 20-incubator run across both
roughly doubles how much of the daily workload actually completes before either quota is
exhausted - and if one provider fails mid-incubator (rate limit, empty response), the other
picks up that same incubator as a fallback rather than losing the run entirely.

This is NOT the same thing as running multiple accounts on one provider to evade its rate
limit (see project discussion) - these are two separate, legitimately-provisioned services,
used the way a router is supposed to use multiple backends.
"""

from __future__ import annotations

import os

from crewai import LLM

PROVIDERS = ("openrouter", "gemini")


def available_providers() -> list[str]:
    """Which providers actually have a usable key configured, in preference order."""
    found = []
    if os.getenv("OPENROUTER_API_KEY"):
        found.append("openrouter")
    if os.getenv("GEMINI_API_KEY"):
        found.append("gemini")
    if not found:
        raise RuntimeError(
            "No LLM provider configured. Set OPENROUTER_API_KEY and/or GEMINI_API_KEY "
            "in .env (copy .env.example first)."
        )
    return found


def build_llm(provider: str) -> LLM:
    if provider == "openrouter":
        model = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-20b:free")
        return LLM(
            model=f"openrouter/{model}",
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            temperature=0.2,
        )
    if provider == "gemini":
        # CrewAI routes "gemini/<model>" through its native Google Gen AI provider (needs
        # the google-genai extra, see requirements.txt). That SDK reads GOOGLE_API_KEY or
        # GEMINI_API_KEY from the env; we mirror our GEMINI_API_KEY into GOOGLE_API_KEY too
        # so it's found however the provider looks for it. Check
        # ai.google.dev/gemini-api/docs/models for the current free-tier model name if this
        # one has been retired - same rotation risk as the OpenRouter default, see README.
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key and not os.getenv("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = gemini_key
        # gemini-3.1-flash-lite: current free-tier model with the highest daily quota
        # (~1000 req/day), which suits the many-call agentic loop. Google retired the 2.5
        # series for new API keys in 2026 - if this 404s as "no longer available", check
        # ai.google.dev/gemini-api/docs/models for the current free id and set GEMINI_MODEL.
        model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
        return LLM(
            model=f"gemini/{model}",
            api_key=gemini_key,
            temperature=0.2,
        )
    raise ValueError(f"Unknown provider: {provider!r}")


def other_provider(provider: str, providers: list[str]) -> str | None:
    """The provider to fall back to, if any is configured besides the one that just failed."""
    remaining = [p for p in providers if p != provider]
    return remaining[0] if remaining else None


def looks_like_quota_error(exc: Exception) -> bool:
    """Best-effort sniff of 'this provider is out of budget right now' vs a real bug -
    only the former should trigger a fallback to the other provider."""
    text = str(exc).lower()
    signals = (
        "429",
        "rate limit",
        "rate_limit",
        "quota",
        "resource_exhausted",
        "resourceexhausted",
        "invalid response from llm call",  # the empty-response symptom seen on free tiers
    )
    return any(s in text for s in signals)
