from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from app.adapters.base import RawListing
from app.adapters.boards import fetchers
from app.services.config_loader import load_job_sites

logger = logging.getLogger(__name__)

AdapterFn = Callable[[], Awaitable[list[RawListing]]]

ADAPTERS: dict[str, AdapterFn] = {
    "remoteok": fetchers.fetch_remoteok,
    "remotive": fetchers.fetch_remotive,
    "jobicy": fetchers.fetch_jobicy,
    "arbeitnow": fetchers.fetch_arbeitnow,
    "himalayas": fetchers.fetch_himalayas,
    "weworkremotely": fetchers.fetch_weworkremotely,
    "themuse": fetchers.fetch_themuse,
    "hn_whoishiring": fetchers.fetch_hn_whoishiring,
    "jobright": fetchers.fetch_jobright,
}


async def discover_feeds(enabled_only: bool = True) -> list[RawListing]:
    cfg = load_job_sites()
    sites = cfg.get("sites") or []
    listings: list[RawListing] = []
    tasks: list[tuple[str, Awaitable[list[RawListing]]]] = []
    ats_adapters = {
        "greenhouse",
        "lever",
        "ashby",
        "workable",
        "indeed",
        "glassdoor",
        "dice",
        "wellfound",
    }

    for site in sites:
        if enabled_only and not site.get("enabled", False):
            continue
        adapter = site.get("adapter")
        if adapter in ats_adapters:
            continue
        fn = ADAPTERS.get(adapter)
        if not fn:
            logger.warning("No adapter for %s", adapter)
            continue
        tasks.append((site.get("name") or adapter, fn()))

    results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
    for (name, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            logger.exception("Discover failed for %s: %s", name, result)
            continue
        listings.extend(result)
        logger.info("%s: %d listings", name, len(result))
    return listings


async def fetch_ats_board(source: str, token: str) -> list[RawListing]:
    source = (source or "").strip().lower()
    token = (token or "").strip()
    if source == "greenhouse":
        return await fetchers.fetch_greenhouse_board(token)
    if source == "lever":
        return await fetchers.fetch_lever_board(token)
    if source == "ashby":
        return await fetchers.fetch_ashby_board(token)
    if source == "workable":
        return await fetchers.fetch_workable_board(token)
    return []


async def discover_all(enabled_only: bool = True) -> list[RawListing]:
    listings = await discover_feeds(enabled_only=enabled_only)
    cfg = load_job_sites()
    sites = cfg.get("sites") or []

    async def _pull_token_boards(
        site_adapter: str,
        tokens_key: str,
        fetch_one,
        label: str,
    ) -> None:
        enabled = any(s.get("adapter") == site_adapter and s.get("enabled") for s in sites)
        if not enabled:
            return
        for token in cfg.get(tokens_key) or []:
            try:
                rows = await fetch_one(token)
                listings.extend(rows)
                logger.info("%s %s: %d listings", label, token, len(rows))
            except Exception as e:
                logger.warning("%s %s failed: %s", label, token, e)

    await _pull_token_boards(
        "greenhouse", "greenhouse_boards", fetchers.fetch_greenhouse_board, "Greenhouse"
    )
    await _pull_token_boards("lever", "lever_boards", fetchers.fetch_lever_board, "Lever")
    await _pull_token_boards("ashby", "ashby_boards", fetchers.fetch_ashby_board, "Ashby")
    return listings
