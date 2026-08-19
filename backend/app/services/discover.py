from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.adapters.base import RawListing
from app.adapters.boards import discover_feeds, fetch_ats_board
from app.db.models import AtsBoard
from app.db.session import get_session_factory
from app.services.ats_catalog import catalog_stats, mark_board, next_unchecked_boards, seed_ats_catalog
from app.services.config_loader import load_job_sites
from app.validators.dedupe import normalize_company

log = logging.getLogger("poong.discover")

DEFAULT_QUERIES = (
    "AI Engineer",
    "Machine Learning Engineer",
    "LLM Engineer",
    "Software Engineer",
    "Frontend Engineer",
    "Front End Engineer",
    "Frontend Developer",
    "Backend Engineer",
    "Backend Developer",
    "Full Stack Engineer",
    "Full-Stack Engineer",
    "Full Stack Developer",
    "Data Engineer",
    "DevOps Engineer",
    "Platform Engineer",
    "React Engineer",
    "React Developer",
    "Node.js Engineer",
    "TypeScript Engineer",
)


def load_discovery_settings() -> dict[str, Any]:
    data = load_job_sites()
    settings = data.get("settings") or {}
    disc = data.get("discovery") or {}
    queries = disc.get("queries") or list(DEFAULT_QUERIES)
    sites = data.get("sites") or []
    enabled_feeds = sum(1 for s in sites if s.get("enabled"))
    board_tokens = (
        len(data.get("greenhouse_boards") or [])
        + len(data.get("lever_boards") or [])
        + len(data.get("ashby_boards") or [])
    )
    insert_limit = int(
        disc.get("max_jobs")
        or settings.get("batch_insert_limit")
        or 50
    )
    return {
        "posted_within_days": int(disc.get("posted_within_days") or 14),
        "insert_limit": max(1, insert_limit),
        "boards_per_run": max(5, int(disc.get("boards_per_run") or 30)),
        "queries": [str(q).strip() for q in queries if str(q).strip()],
        "us_remote_strict": bool(settings.get("us_remote_strict", True)),
        "sites_enabled": enabled_feeds,
        "board_tokens": board_tokens,
    }


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000.0
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _recent(dt: datetime | None, cutoff: datetime) -> bool:
    if dt is None:
        return True
    return dt >= cutoff


def _looks_us_remote(listing: RawListing) -> bool:
    location = listing.location_raw or ""
    description = (listing.description or "")[:1200]
    blob = f"{location} {description} {listing.job_title}".lower()
    if listing.is_remote_flag_from_board is False:
        if "remote" not in blob:
            return False
    if any(x in blob for x in ("onsite", "on-site", "hybrid", "in office", "in-office")):
        if "remote" not in blob:
            return False
    if any(x in blob for x in ("europe only", "uk only", "india only", "emea only", "canada only")):
        return False
    if any(x in blob for x in ("united states", "usa", ", us", "u.s.", "us only", "us-only")):
        return True
    if "remote" in blob and "worldwide" not in blob and "anywhere" not in blob:
        return True
    if "worldwide" in blob or "anywhere" in blob:
        return True
    return bool(listing.is_remote_flag_from_board) or "remote" in blob or not location.strip()


def _matches_query(listing: RawListing, queries: list[str]) -> bool:
    if not queries:
        return True
    hay = f"{listing.job_title} {(listing.description or '')[:400]}".lower()
    for q in queries:
        ql = q.lower()
        if ql in hay:
            return True
        parts = ql.split()
        if parts and all(p in hay for p in parts):
            return True
    return False


def _us_priority(listing: RawListing) -> int:
    loc = (listing.location_raw or "").lower()
    blob = f"{listing.job_title} {loc}".lower()
    score = 0
    if listing.is_remote_flag_from_board:
        score += 2
    if any(x in blob for x in ("united states", "usa", "u.s", "us only", "us-only", "remote us")):
        score += 5
    if "remote" in blob:
        score += 2
    if any(x in blob for x in ("hybrid", "onsite", "on-site", "in-office")):
        score -= 10
    return score


def diversify_by_company(listings: list[RawListing]) -> list[RawListing]:
    """One best role per company first, shuffled company order."""
    by_co: dict[str, list[RawListing]] = {}
    for listing in listings:
        key = normalize_company(listing.company_name) or listing.company_name.lower()
        by_co.setdefault(key, []).append(listing)
    groups: list[list[RawListing]] = []
    for group in by_co.values():
        group.sort(key=_us_priority, reverse=True)
        groups.append(group[:8])
    random.shuffle(groups)
    out: list[RawListing] = []
    for group in groups:
        if group:
            out.append(group[0])
    return out


def listing_to_dict(listing: RawListing) -> dict[str, Any]:
    url = (listing.job_url or listing.canonical_url or "").strip()
    return {
        "company": listing.company_name.strip(),
        "title": listing.job_title.strip(),
        "url": url,
        "location": (listing.location_raw or "Remote").strip(),
        "description": (listing.description or "").strip() or None,
        "source": listing.source,
        "external_id": listing.external_id,
        "staffing_firm": listing.staffing_firm,
    }


def _filter_listings(
    listings: list[RawListing],
    *,
    cutoff: datetime,
    queries: list[str],
    us_remote_strict: bool,
) -> tuple[list[RawListing], dict[str, int]]:
    by_source: dict[str, int] = {}
    seen_url: set[str] = set()
    filtered: list[RawListing] = []

    for listing in listings:
        url = (listing.canonical_url or listing.job_url or "").strip().lower().rstrip("/")
        if not url or not listing.company_name.strip() or not listing.job_title.strip():
            continue
        if url in seen_url:
            continue
        posted = _parse_dt(listing.posted_at)
        if not _recent(posted, cutoff):
            continue
        if not _matches_query(listing, queries):
            continue
        if us_remote_strict and not _looks_us_remote(listing):
            continue
        seen_url.add(url)
        filtered.append(listing)
        by_source[listing.source] = by_source.get(listing.source, 0) + 1

    return filtered, by_source


async def collect_new_listings() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cfg = load_discovery_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg["posted_within_days"])
    factory = get_session_factory()

    async with factory() as session:
        seeded = await seed_ats_catalog(session)
        await session.commit()
        boards = await next_unchecked_boards(session, limit=cfg["boards_per_run"])
        ats_meta = await catalog_stats(session)

    log.info(
        "discover: feeds + %s unsearched ATS boards (seeded=%s pending=%s)",
        len(boards),
        seeded,
        ats_meta.get("pending"),
    )
    raw = await discover_feeds(enabled_only=True)
    ats_this_run: list[dict[str, Any]] = []
    async with factory() as session:
        for bid in [b.id for b in boards]:
            row = await session.get(AtsBoard, bid)
            if not row:
                continue
            try:
                listings = await fetch_ats_board(row.source, row.token)
                await mark_board(
                    session,
                    row,
                    jobs_found=len(listings),
                    company_name=listings[0].company_name if listings else row.token,
                )
                raw.extend(listings)
                ats_this_run.append(
                    {
                        "source": row.source,
                        "token": row.token,
                        "company": row.company_name,
                        "jobs_found": len(listings),
                        "status": row.status,
                    }
                )
                log.info("ATS %s/%s: %s jobs", row.source, row.token, len(listings))
            except Exception as exc:  # noqa: BLE001
                await mark_board(session, row, jobs_found=0, error=str(exc)[:400])
                ats_this_run.append(
                    {
                        "source": row.source,
                        "token": row.token,
                        "jobs_found": 0,
                        "status": "failed",
                        "error": str(exc)[:200],
                    }
                )
                log.warning("ATS %s/%s failed: %s", row.source, row.token, exc)
        await session.commit()
        ats_meta = await catalog_stats(session)

    filtered, by_source = _filter_listings(
        raw,
        cutoff=cutoff,
        queries=cfg["queries"],
        us_remote_strict=cfg["us_remote_strict"],
    )
    diversified = diversify_by_company(filtered)
    insert_limit = cfg["insert_limit"]
    candidates = diversified[: insert_limit * 3]

    meta = {
        "posted_within_days": cfg["posted_within_days"],
        "queries": cfg["queries"],
        "discovered_raw": len(raw),
        "after_filters": len(filtered),
        "unique_companies": len(diversified),
        "by_source": by_source,
        "candidates": len(candidates),
        "insert_limit": insert_limit,
        "sites_enabled": cfg["sites_enabled"],
        "board_tokens": cfg["board_tokens"],
        "ats_this_run": len(ats_this_run),
        "ats_pending": ats_meta.get("pending"),
        "ats_checked": ats_meta.get("checked"),
        "ats_total": ats_meta.get("total"),
        "ats_with_jobs": ats_meta.get("with_jobs"),
        "ats_boards": ats_this_run[:40],
    }
    log.info(
        "discover: raw=%s filtered=%s companies=%s candidates=%s ats=%s/%s pending",
        len(raw),
        len(filtered),
        len(diversified),
        len(candidates),
        ats_meta.get("checked"),
        ats_meta.get("total"),
    )
    return [listing_to_dict(x) for x in candidates], meta
