"""Catalog of fetchable job sites for site-scoped discovery UI."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
        "id": "jobright",
        "label": "Jobright",
        "kind": "feed",
        "blurb": "Jobright remote jobs — searches every title you pick",
        # Both routes read at 4s a request, so this is a request budget, and
        # what it buys depends on which route the install can take.
        #
        # Signed in, a title costs one call to select the search plus one per
        # twenty jobs it matched — four or five requests for a day of one
        # title, so a 17-title domain lands in about five minutes and every
        # domain at once still fits.
        #
        # Without a cookie it is fourteen pages per title: two employment
        # pools (full time, contract/part-time) across the bare search and six
        # seniority levels, because a public page shows thirty jobs and has no
        # second page. A 17-title domain is 238 pages, near 21 minutes, and
        # selecting every domain is 1176 and will not fit.
        #
        # 30 minutes covers the slow route. Either way the fetcher stops
        # itself 20s before the deadline and returns what it found rather than
        # being cancelled with everything still in hand.
        "timeout": 1800,
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

# `max_jobs` is what a window may return. A narrow window is already bounded
# by its own date filter -- a day of remote listings is a few hundred, not a
# few thousand -- so capping it at 100 only threw away jobs that had been
# fetched, filtered and asked for. "All time" still needs a real ceiling.
WINDOWS: list[dict[str, Any]] = [
    {
        "id": "24h",
        "label": "Last 24h",
        "days": 1,
        "require_posted_at": True,
        "max_jobs": 1000,
    },
    {
        "id": "7d",
        "label": "7 days",
        "days": 7,
        "require_posted_at": False,
        "max_jobs": 500,
    },
    {
        "id": "all",
        "label": "All time",
        "days": 3650,
        "require_posted_at": False,
        "max_jobs": 100,
    },
]

# The ceiling no window may exceed.
MAX_JOBS_CAP = 1000


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


def window_cutoff(window: str | None, now: datetime | None = None) -> datetime:
    """The earliest posting time a window accepts, in UTC.

    A rolling window: "last 24h" is the 24 hours before this moment, not the
    calendar day. Every posting time is compared as an instant, so the answer
    is the same wherever the user is — what changes is how the times read on
    screen, and those are rendered in their own timezone.
    """
    moment = now or datetime.now(timezone.utc)
    return moment - timedelta(days=int(resolve_window(window)["days"]))


def window_cap(window: str | None) -> int:
    """How many jobs a window may return, bounded by the absolute ceiling."""
    win = resolve_window(window)
    return max(1, min(MAX_JOBS_CAP, int(win.get("max_jobs") or MAX_JOBS_CAP)))


def resolve_site(site: str | None) -> dict[str, Any] | None:
    sid = (site or "").strip().lower()
    for row in FETCH_SITES:
        if row["id"] == sid:
            return dict(row)
    return None
