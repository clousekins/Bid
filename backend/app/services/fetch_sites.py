"""Catalog of fetchable job sites for site-scoped discovery UI."""
from __future__ import annotations

from typing import Any

# Public APIs / ATS boards only — no LinkedIn/Indeed/Dice scrapers.
FETCH_SITES: list[dict[str, Any]] = [
    {
        "id": "greenhouse",
        "label": "Greenhouse",
        "kind": "ats",
        "blurb": "Company career boards on Greenhouse",
    },
    {
        "id": "ashby",
        "label": "Ashby",
        "kind": "ats",
        "blurb": "Company career boards on Ashby",
    },
    {
        "id": "lever",
        "label": "Lever",
        "kind": "ats",
        "blurb": "Company career boards on Lever",
    },
    {
        "id": "workable",
        "label": "Workable",
        "kind": "ats",
        "blurb": "Company career boards on Workable",
    },
    {
        "id": "companies",
        "label": "All company ATS",
        "kind": "ats_all",
        "blurb": "Rotate unsearched Greenhouse/Lever/Ashby/Workable boards",
    },
    {
        "id": "remoteok",
        "label": "Remote OK",
        "kind": "feed",
        "blurb": "Public RemoteOK JSON feed",
    },
    {
        "id": "remotive",
        "label": "Remotive",
        "kind": "feed",
        "blurb": "Public Remotive jobs API",
    },
    {
        "id": "weworkremotely",
        "label": "We Work Remotely",
        "kind": "feed",
        "blurb": "WWR programming RSS feeds",
    },
    {
        "id": "himalayas",
        "label": "Himalayas",
        "kind": "feed",
        "blurb": "Himalayas remote jobs API",
    },
    {
        "id": "jobicy",
        "label": "Jobicy",
        "kind": "feed",
        "blurb": "Jobicy remote jobs API",
    },
    {
        "id": "arbeitnow",
        "label": "Arbeitnow",
        "kind": "feed",
        "blurb": "Arbeitnow public job board API",
    },
    {
        "id": "themuse",
        "label": "The Muse",
        "kind": "feed",
        "blurb": "The Muse public jobs API",
    },
    {
        "id": "hn_whoishiring",
        "label": "HN Who is Hiring",
        "kind": "feed",
        "blurb": "Monthly Ask HN: Who is hiring? thread",
    },
]

WINDOWS: list[dict[str, Any]] = [
    {"id": "24h", "label": "24h", "days": 1, "require_posted_at": True},
    {"id": "7d", "label": "7 days", "days": 7, "require_posted_at": False},
    {"id": "all", "label": "All time", "days": 3650, "require_posted_at": False},
]

MAX_JOBS_CAP = 100


def list_fetch_sites() -> dict[str, Any]:
    return {
        "sites": FETCH_SITES,
        "windows": WINDOWS,
        "max_jobs": MAX_JOBS_CAP,
        "notes": (
            "Dice, Indeed, Glassdoor, Wellfound, and LinkedIn are not available "
            "(no reliable public API). Use company ATS boards + public feeds instead."
        ),
    }


def resolve_window(window: str | None) -> dict[str, Any]:
    wid = (window or "7d").strip().lower()
    for row in WINDOWS:
        if row["id"] == wid:
            return dict(row)
    return dict(WINDOWS[1])  # 7d default


def resolve_site(site: str | None) -> dict[str, Any] | None:
    sid = (site or "").strip().lower()
    for row in FETCH_SITES:
        if row["id"] == sid:
            return dict(row)
    return None
