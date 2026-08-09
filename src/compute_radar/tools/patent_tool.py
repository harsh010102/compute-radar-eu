"""EPO Open Patent Services (OPS) patent-verification.

Given a company (applicant) or founder (inventor) name, query EPO's official free OPS
REST API for European patent filings and return a compact signal: how many matching
applications exist, a few sample publication numbers, and whether any are EP documents.
A real EP filing is a strong "this differentiation is more than marketing" signal — the
same thing the EPO Deep Tech Finder surfaces, but via the official API (in-ToS, no
scraping of the DTF frontend, which actively blocks bots).

Auth: OAuth2 client-credentials. Register a free "non-paying" application at
https://developers.epo.org to get a consumer key + secret, then set OPS_CONSUMER_KEY /
OPS_CONSUMER_SECRET in .env (or as GitHub secrets). Without them, every lookup returns
None and patent enrichment is skipped gracefully — nothing else in the pipeline changes.

Deliberately hand-rolled on `requests` (no python-epo-ops-client dependency) so the
graceful-skip path and error handling are fully under our control. OPS JSON is a
1:1 machine translation of its XML, so it's parsed defensively (recursive key search)
rather than against a brittle fixed path.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from typing import Any

import requests

OPS_BASE = "https://ops.epo.org/3.2"
AUTH_URL = f"{OPS_BASE}/auth/accesstoken"
SEARCH_URL = f"{OPS_BASE}/rest-services/published-data/search"

_token: dict[str, Any] = {"value": None, "exp": 0.0}
_lock = threading.Lock()
_last_call = {"t": 0.0}


class PatentThrottleError(Exception):
    """OPS said slow down / not now (429/403). Callers should stop enriching, not crash."""


def is_configured() -> bool:
    return bool(os.getenv("OPS_CONSUMER_KEY") and os.getenv("OPS_CONSUMER_SECRET"))


def _get_token() -> str | None:
    key = os.getenv("OPS_CONSUMER_KEY")
    secret = os.getenv("OPS_CONSUMER_SECRET")
    if not key or not secret:
        return None
    with _lock:
        if _token["value"] and time.time() < _token["exp"] - 30:
            return _token["value"]
        cred = base64.b64encode(f"{key}:{secret}".encode()).decode()
        resp = requests.post(
            AUTH_URL,
            headers={
                "Authorization": f"Basic {cred}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        _token["value"] = payload["access_token"]
        _token["exp"] = time.time() + int(payload.get("expires_in", 1200))
        return _token["value"]


def _polite() -> None:
    """OPS publishes throttling headers; for our low volume a steady spacing is enough."""
    gap = float(os.getenv("OPS_DELAY_SECONDS", "1.5"))
    wait = gap - (time.time() - _last_call["t"])
    if wait > 0:
        time.sleep(wait)
    _last_call["t"] = time.time()


def _find_all(node: Any, key: str) -> list[Any]:
    """Recursively collect every value stored under `key` anywhere in a nested dict/list."""
    found: list[Any] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            found.extend(_find_all(v, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_all(item, key))
    return found


def _first_int(values: list[Any]) -> int:
    for v in values:
        try:
            return int(str(v).strip())
        except (ValueError, TypeError):
            continue
    return 0


def _extract_pub_numbers(data: dict) -> list[str]:
    """Pull sample publication numbers from OPS search JSON, defensively.

    Each result carries a `document-id` with country / doc-number / kind fields, each
    usually of the OPS `{"$": "..."}` text-node shape. Format as e.g. 'EP4012345A1'.
    """
    out: list[str] = []
    for doc in _find_all(data, "document-id"):
        docs = doc if isinstance(doc, list) else [doc]
        for d in docs:
            if not isinstance(d, dict):
                continue
            def txt(field: str) -> str:
                v = d.get(field)
                if isinstance(v, dict):
                    return str(v.get("$", "")).strip()
                return str(v or "").strip()
            country, number, kind = txt("country"), txt("doc-number"), txt("kind")
            if number:
                out.append(f"{country}{number}{kind}")
    # de-dup, keep order
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x); uniq.append(x)
    return uniq


def _lookup(cql: str, query_name: str, max_records: int) -> dict | None:
    token = _get_token()
    if token is None:
        return None  # not configured -> skip
    _polite()
    resp = requests.get(
        SEARCH_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"q": cql, "Range": f"1-{max_records}"},
        timeout=25,
    )
    if resp.status_code == 404:
        # OPS returns 404 for a valid search with zero hits.
        return {"query_name": query_name, "patent_count": 0, "has_ep_patents": False,
                "samples": [], "source": "EPO OPS"}
    if resp.status_code in (403, 429):
        raise PatentThrottleError(f"OPS throttled/forbidden ({resp.status_code}) for {query_name!r}")
    if resp.status_code == 401:
        # token likely expired mid-run; drop it so the next call refreshes
        _token["value"] = None
        raise PatentThrottleError("OPS token rejected (401) - will refresh")
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        return None  # unexpected non-JSON; don't guess
    count = _first_int(_find_all(data, "@total-result-count"))
    samples = _extract_pub_numbers(data)[:max_records]
    return {
        "query_name": query_name,
        "patent_count": count,
        "has_ep_patents": any(s.startswith("EP") for s in samples) or count > 0,
        "samples": samples[:6],
        "source": "EPO OPS",
    }


def lookup_applicant_patents(name: str, max_records: int = 10) -> dict | None:
    """EP filings where `name` is the applicant (i.e. the company). None if OPS unconfigured."""
    safe = name.replace('"', "").strip()
    if not safe:
        return None
    return _lookup(f'pa="{safe}"', name, max_records)


def lookup_inventor_patents(name: str, max_records: int = 10) -> dict | None:
    """EP filings where `name` is a named inventor (i.e. a founder). None if unconfigured."""
    safe = name.replace('"', "").strip()
    if not safe:
        return None
    return _lookup(f'in="{safe}"', name, max_records)
