"""you.com search wrapper.

Three things live here and nowhere else:
  1. retry with backoff
  2. a disk cache so re-running the graph during development is free
  3. a SearchError that nodes can catch and degrade on

Keeping failure handling in the tool layer means the nodes stay readable.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import requests

CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# you.com v1. The older api.ydc-index.io/{search,news} endpoints now return
# 403 for current keys, and v1 has no separate news endpoint - news comes back
# from /v1/search as its own block when `freshness` is set.
YDC_ENDPOINT = "https://ydc-index.io/v1/search"

MAX_ATTEMPTS = 3
BACKOFF_BASE = 1.5


class SearchError(RuntimeError):
    """Raised when search fails after all retries. Nodes catch this."""


def _cache_key(query: str, kind: str) -> Path:
    h = hashlib.sha256(f"{kind}:{query}".encode()).hexdigest()[:16]
    return CACHE_DIR / f"{kind}_{h}.json"


def search(query: str, kind: str = "web", num: int = 5) -> list[dict]:
    """Return a list of {title, url, snippet}. Raises SearchError on failure.

    Set NO_CACHE=1 to bypass the cache (do this for the live demo).
    """
    cache_file = _cache_key(query, kind)
    if cache_file.exists() and not os.getenv("NO_CACHE"):
        return json.loads(cache_file.read_text(encoding="utf-8"))

    api_key = os.getenv("YDC_API_KEY")
    if not api_key:
        # This is the failure you unplug on camera.
        raise SearchError("YDC_API_KEY is not set")

    params = {"query": query, "count": num}
    if kind == "news":
        # v1 returns a `news` block only when a recency window is requested.
        params["freshness"] = "month"
    last_err = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.get(
                YDC_ENDPOINT,
                params=params,
                headers={"X-API-Key": api_key},
                timeout=20,
            )
            resp.raise_for_status()
            results = _normalize(resp.json(), kind)
            if not results:
                raise SearchError(f"no results for {query!r}")
            cache_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
            return results
        except (requests.RequestException, SearchError, ValueError) as e:
            last_err = e
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BACKOFF_BASE ** attempt)

    raise SearchError(f"search failed after {MAX_ATTEMPTS} attempts: {last_err}")


def _normalize(payload: dict, kind: str) -> list[dict]:
    """Flatten a /v1/search payload to [{title, url, snippet, date?}].

    Both blocks live under `results`. A news query falls back to the web
    block, since `freshness` narrows the window but does not guarantee that
    you.com classifies anything as news.
    """
    # Always read the web block. you.com's `news` block matches loosely on
    # keywords and returns off-topic headlines (a "Notion news" query comes
    # back with crypto and sports stories); the web block plus `freshness`
    # gives genuinely recent, on-topic company coverage.
    items = payload.get("results", {}).get("web", [])

    out = []
    for i in items:
        # `snippets` is richer when present; `description` is the fallback.
        snippet = " ".join(i.get("snippets") or []) or i.get("description", "")
        entry = {
            "title": i.get("title", ""),
            "url": i.get("url", ""),
            "snippet": snippet[:1500],
        }
        if i.get("page_age"):
            entry["date"] = i["page_age"]
        out.append(entry)
    return out
