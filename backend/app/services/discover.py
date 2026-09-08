from __future__ import annotations

import asyncio
import inspect
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from app.adapters.base import RawListing
from app.adapters.boards import ADAPTERS, discover_feeds, fetch_ats_board
from app.db.models import AtsBoard
from app.db.session import get_session_factory
from app.services.ats_catalog import (
    boards_for_source,
    catalog_stats,
    mark_board,
    next_unchecked_boards,
    seed_ats_catalog,
)
from app.services.config_loader import load_job_sites
from app.services.fetch_sites import (
    MAX_JOBS_CAP,
    resolve_site,
    resolve_window,
    window_cap,
    window_cutoff,
)
from app.services.job_domains import (
    classify_domains,
    domains_storage,
    title_matches_roles,
    matches_allowed,
    normalize_domain_ids,
)
from app.services.employer_resolve import (
    is_job_board_or_aggregator,
    is_staffing_firm,
    resolve_employer,
)
from app.validators.dedupe import normalize_company
from app.validators.it_domain import check_it_domain
from app.validators.remote_us import check_remote_us
from app.validators.seniority import check_seniority

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


def load_discovery_settings(
    *,
    mode: str = "standard",
    window: str | None = None,
    max_jobs: int | None = None,
) -> dict[str, Any]:
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
    posted_within_days = int(disc.get("posted_within_days") or 14)
    boards_per_run = max(5, int(disc.get("boards_per_run") or 30))
    require_posted_at = False
    mode_norm = (mode or "standard").strip().lower()
    if mode_norm in {"fresh", "discover_fresh", "24h"}:
        mode_norm = "fresh"
        posted_within_days = int(disc.get("fresh_posted_within_days") or 1)
        boards_per_run = max(
            boards_per_run,
            int(disc.get("fresh_boards_per_run") or max(boards_per_run, 60)),
        )
        insert_limit = max(insert_limit, int(disc.get("fresh_max_jobs") or insert_limit))
        require_posted_at = True
    else:
        mode_norm = "standard"

    if window:
        win = resolve_window(window)
        posted_within_days = int(win["days"])
        require_posted_at = bool(win["require_posted_at"])
        mode_norm = f"site_{win['id']}"

    # A chosen window sets its own ceiling; the global cap is the backstop for
    # the modes that pick no window at all.
    ceiling = window_cap(window) if window else MAX_JOBS_CAP
    if max_jobs is not None:
        insert_limit = min(ceiling, int(max_jobs))
    else:
        insert_limit = min(ceiling, insert_limit) if not window else ceiling
    insert_limit = max(1, insert_limit)

    return {
        "mode": mode_norm,
        "posted_within_days": posted_within_days,
        "insert_limit": insert_limit,
        "boards_per_run": boards_per_run,
        "queries": [str(q).strip() for q in queries if str(q).strip()],
        "us_remote_strict": bool(settings.get("us_remote_strict", True)),
        "sites_enabled": enabled_feeds,
        "board_tokens": board_tokens,
        "require_posted_at": require_posted_at,
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


def _recent(dt: datetime | None, cutoff: datetime, *, require_posted_at: bool = False) -> bool:
    if dt is None:
        return not require_posted_at
    return dt >= cutoff


def listing_to_dict(listing: RawListing) -> dict[str, Any]:
    url = (listing.job_url or listing.canonical_url or "").strip()
    domain_ids = classify_domains(listing.job_title, listing.description)
    return {
        "company": listing.company_name.strip(),
        "title": listing.job_title.strip(),
        "url": url,
        "location": (listing.location_raw or "Remote").strip(),
        "description": (listing.description or "").strip() or None,
        "source": listing.source,
        "posted_at": listing.posted_at,
        "external_id": listing.external_id,
        "staffing_firm": listing.staffing_firm,
        "domains": domains_storage(domain_ids),
        "domain_ids": domain_ids,
        # Set by adapters that know it; blank everywhere else rather than
        # guessed, since "no answer" and "full time" are not the same thing.
        "employment_type": (listing.raw or {}).get("employment_type"),
    }


async def enrich_listing_employer(listing: dict[str, Any]) -> dict[str, Any]:
    """Resolve real client company from JD when listing company is a board/staffing name."""
    company = str(listing.get("company") or "").strip()
    desc = str(listing.get("description") or "")
    url = str(listing.get("url") or "")
    if not company:
        return listing
    if not (is_job_board_or_aggregator(company) or is_staffing_firm(company)):
        # Still try light heuristic when JD has Client: X
        if "client" not in desc.lower() and "hiring company" not in desc.lower():
            return listing
    resolved = await resolve_employer(
        listing_company=company,
        title=str(listing.get("title") or ""),
        url=url,
        jd=desc[:8000],
    )
    if not resolved.changed:
        return listing
    out = dict(listing)
    out["company"] = resolved.company
    if resolved.source_name and not out.get("staffing_firm"):
        out["staffing_firm"] = resolved.source_name
    elif is_job_board_or_aggregator(company) or is_staffing_firm(company):
        out["staffing_firm"] = out.get("staffing_firm") or company
    return out


def _passes_prefilter(listing: RawListing) -> tuple[bool, str]:
    """Same remote/seniority/IT gates as insert pipeline — skip doomed rows early."""
    location = (listing.location_raw or "Remote").strip()
    description = listing.description or ""
    title = listing.job_title or ""
    remote = check_remote_us(location, description)
    if not remote.ok:
        return False, remote.reason
    seniority = check_seniority(title)
    if not seniority.ok:
        return False, seniority.reason
    it = check_it_domain(title, description[:400] if description else None)
    if not it.ok:
        return False, it.reason
    return True, ""


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


def _count_companies(listings: list[RawListing]) -> int:
    """Distinct employers in a list. The count a diversified list used to be."""
    return len(
        {
            normalize_company(listing.company_name) or listing.company_name.lower()
            for listing in listings
        }
    )


def diversify_by_company(
    listings: list[RawListing], per_company: int = 8
) -> list[RawListing]:
    """Company-diverse order, keeping up to `per_company` roles each.

    Round robin: every company's best role comes first, then everyone's
    second, and so on. So the head of the list is still one role per company
    — no employer can flood it — but a company hiring five of the titles that
    were ticked contributes all five instead of four being thrown away.
    """
    by_co: dict[str, list[RawListing]] = {}
    for listing in listings:
        key = normalize_company(listing.company_name) or listing.company_name.lower()
        by_co.setdefault(key, []).append(listing)
    groups: list[list[RawListing]] = []
    for group in by_co.values():
        group.sort(key=_us_priority, reverse=True)
        groups.append(group[: max(1, per_company)])
    random.shuffle(groups)
    out: list[RawListing] = []
    for rank in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if rank < len(group):
                out.append(group[rank])
    return out


def _filter_listings(
    listings: list[RawListing],
    *,
    cutoff: datetime,
    queries: list[str],
    us_remote_strict: bool,
    require_posted_at: bool = False,
    allowed_domains: list[str] | None = None,
    allowed_roles: list[str] | None = None,
) -> tuple[list[RawListing], dict[str, int], dict[str, int]]:
    by_source: dict[str, int] = {}
    drop_reasons: dict[str, int] = {}
    seen_url: set[str] = set()
    filtered: list[RawListing] = []
    allowed = normalize_domain_ids(allowed_domains) if allowed_domains else []
    wanted_roles = [r for r in (allowed_roles or []) if r]

    def _drop(reason: str) -> None:
        drop_reasons[reason] = drop_reasons.get(reason, 0) + 1

    for listing in listings:
        url = (listing.canonical_url or listing.job_url or "").strip().lower().rstrip("/")
        if not url or not listing.company_name.strip() or not listing.job_title.strip():
            _drop("missing_fields")
            continue
        if url in seen_url:
            _drop("duplicate_url")
            continue
        posted = _parse_dt(listing.posted_at)
        if not _recent(posted, cutoff, require_posted_at=require_posted_at):
            _drop("too_old" if posted is not None else "missing_posted_at")
            continue
        if not _matches_query(listing, queries):
            _drop("query_mismatch")
            continue
        if us_remote_strict:
            ok, reason = _passes_prefilter(listing)
            if not ok:
                _drop(reason or "prefilter")
                continue
        if allowed:
            domain_ids = classify_domains(listing.job_title, listing.description)
            # A domain says the field; a role says the job. Selecting
            # "Senior Data Engineer" should not return every data engineer.
            if wanted_roles and not title_matches_roles(listing.job_title, wanted_roles):
                _drop("role_mismatch")
                continue
            if not matches_allowed(domain_ids, allowed):
                _drop("domain_mismatch")
                continue
        seen_url.add(url)
        filtered.append(listing)
        by_source[listing.source] = by_source.get(listing.source, 0) + 1

    return filtered, by_source, drop_reasons


async def collect_new_listings(
    *,
    mode: str = "standard",
    domains: list[str] | None = None,
    roles: list[str] | None = None,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cfg = load_discovery_settings(mode=mode)
    allowed_domains = normalize_domain_ids(domains)
    cfg["domains"] = allowed_domains
    cfg["roles"] = [r for r in (roles or []) if r]
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg["posted_within_days"])
    factory = get_session_factory()

    async with factory() as session:
        seeded = await seed_ats_catalog(session)
        await session.commit()
        boards = await next_unchecked_boards(session, limit=cfg["boards_per_run"])
        ats_meta = await catalog_stats(session)

    log.info(
        "discover[%s]: feeds + %s unsearched ATS boards (seeded=%s pending=%s days=%s domains=%s)",
        cfg["mode"],
        len(boards),
        seeded,
        ats_meta.get("pending"),
        cfg["posted_within_days"],
        allowed_domains or "all",
    )
    if on_progress:
        await on_progress(
            {
                "phase": "feeds",
                "mode": cfg["mode"],
                "domains": allowed_domains,
                "boards_total": len(boards),
                "boards_done": 0,
                "done": 0,
                "total": max(1, len(boards)),
            }
        )
    raw = await discover_feeds(enabled_only=True)
    ats_this_run: list[dict[str, Any]] = []
    board_ids = [b.id for b in boards]
    sem = asyncio.Semaphore(8)
    lock = asyncio.Lock()
    boards_done = 0

    async def _one_board(bid: int) -> tuple[list[RawListing], dict[str, Any]]:
        nonlocal boards_done
        async with sem:
            async with factory() as session:
                row = await session.get(AtsBoard, bid)
                if not row:
                    return [], {}
                try:
                    await asyncio.sleep(0.05)  # soft rate pacing
                    async with asyncio.timeout(25):
                        listings = await fetch_ats_board(row.source, row.token)
                    company_name = row.token
                    if listings:
                        company_name = listings[0].company_name or row.token
                    elif row.source == "greenhouse" and row.company_name:
                        company_name = row.company_name
                    await mark_board(
                        session,
                        row,
                        jobs_found=len(listings),
                        company_name=company_name,
                    )
                    await session.commit()
                    meta = {
                        "source": row.source,
                        "token": row.token,
                        "company": row.company_name,
                        "jobs_found": len(listings),
                        "status": row.status,
                    }
                    log.info("ATS %s/%s: %s jobs", row.source, row.token, len(listings))
                    return listings, meta
                except Exception as exc:  # noqa: BLE001
                    err = str(exc)[:400]
                    if isinstance(exc, TimeoutError):
                        err = f"timeout fetching {row.source}/{row.token}"
                    await mark_board(session, row, jobs_found=0, error=err)
                    await session.commit()
                    meta = {
                        "source": row.source,
                        "token": row.token,
                        "jobs_found": 0,
                        "status": row.status,
                        "error": err[:200],
                    }
                    log.warning("ATS %s/%s failed: %s", row.source, row.token, exc)
                    return [], meta
                finally:
                    async with lock:
                        boards_done += 1
                        done_n = boards_done
                    if on_progress:
                        await on_progress(
                            {
                                "phase": "ats_fetch",
                                "mode": cfg["mode"],
                                "boards_done": done_n,
                                "boards_total": len(board_ids),
                                "done": done_n,
                                "total": max(1, len(board_ids)),
                            }
                        )

    board_results = await asyncio.gather(*(_one_board(bid) for bid in board_ids))
    for listings, meta in board_results:
        if listings:
            raw.extend(listings)
        if meta:
            ats_this_run.append(meta)

    async with factory() as session:
        ats_meta = await catalog_stats(session)

    filtered, by_source, drop_reasons = _filter_listings(
        raw,
        cutoff=cutoff,
        queries=cfg["queries"],
        us_remote_strict=cfg["us_remote_strict"],
        require_posted_at=cfg["require_posted_at"],
        allowed_domains=allowed_domains or None,
        allowed_roles=cfg.get("roles") or None,
    )
    diversified = diversify_by_company(filtered)
    insert_limit = cfg["insert_limit"]
    candidates = diversified[: insert_limit * 3]

    out: list[dict[str, Any]] = []
    employer_resolved = 0
    for listing in candidates:
        row = listing_to_dict(listing)
        before = row.get("company")
        row = await enrich_listing_employer(row)
        if row.get("company") != before:
            employer_resolved += 1
        out.append(row)

    meta = {
        "mode": cfg["mode"],
        "domains": allowed_domains,
        "posted_within_days": cfg["posted_within_days"],
        "queries": cfg["queries"],
        "discovered_raw": len(raw),
        "after_filters": len(filtered),
        "prefilter_drops": drop_reasons,
        "unique_companies": _count_companies(diversified),
        "by_source": by_source,
        "candidates": len(out),
        "insert_limit": insert_limit,
        "sites_enabled": cfg["sites_enabled"],
        "board_tokens": cfg["board_tokens"],
        "employer_resolved": employer_resolved,
        "ats_this_run": len(ats_this_run),
        "ats_pending": ats_meta.get("pending"),
        "ats_checked": ats_meta.get("checked"),
        "ats_total": ats_meta.get("total"),
        "ats_with_jobs": ats_meta.get("with_jobs"),
        "ats_boards": ats_this_run[:40],
        "boards_done": len(board_ids),
        "boards_total": len(board_ids),
    }
    log.info(
        "discover[%s]: raw=%s filtered=%s companies=%s candidates=%s resolved=%s ats=%s/%s",
        cfg["mode"],
        len(raw),
        len(filtered),
        len(diversified),
        len(out),
        employer_resolved,
        ats_meta.get("checked"),
        ats_meta.get("total"),
    )
    return out, meta


async def _fetch_boards(
    board_ids: list[int],
    *,
    cfg: dict[str, Any],
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None,
) -> tuple[list[RawListing], list[dict[str, Any]]]:
    factory = get_session_factory()
    raw: list[RawListing] = []
    ats_this_run: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(8)
    lock = asyncio.Lock()
    boards_done = 0

    async def _one_board(bid: int) -> tuple[list[RawListing], dict[str, Any]]:
        nonlocal boards_done
        async with sem:
            async with factory() as session:
                row = await session.get(AtsBoard, bid)
                if not row:
                    return [], {}
                try:
                    await asyncio.sleep(0.05)
                    async with asyncio.timeout(25):
                        listings = await fetch_ats_board(row.source, row.token)
                    company_name = row.token
                    if listings:
                        company_name = listings[0].company_name or row.token
                    elif row.source == "greenhouse" and row.company_name:
                        company_name = row.company_name
                    await mark_board(
                        session,
                        row,
                        jobs_found=len(listings),
                        company_name=company_name,
                    )
                    await session.commit()
                    meta = {
                        "source": row.source,
                        "token": row.token,
                        "company": row.company_name,
                        "jobs_found": len(listings),
                        "status": row.status,
                    }
                    return listings, meta
                except Exception as exc:  # noqa: BLE001
                    err = str(exc)[:400]
                    if isinstance(exc, TimeoutError):
                        err = f"timeout fetching {row.source}/{row.token}"
                    await mark_board(session, row, jobs_found=0, error=err)
                    await session.commit()
                    return [], {
                        "source": row.source,
                        "token": row.token,
                        "jobs_found": 0,
                        "status": "failed",
                        "error": err[:200],
                    }
                finally:
                    async with lock:
                        boards_done += 1
                        done_n = boards_done
                    if on_progress:
                        await on_progress(
                            {
                                "phase": "ats_fetch",
                                "mode": cfg["mode"],
                                "boards_done": done_n,
                                "boards_total": len(board_ids),
                                "done": done_n,
                                "total": max(1, len(board_ids)),
                            }
                        )

    if not board_ids:
        return raw, ats_this_run
    for listings, meta in await asyncio.gather(*(_one_board(bid) for bid in board_ids)):
        if listings:
            raw.extend(listings)
        if meta:
            ats_this_run.append(meta)
    return raw, ats_this_run


def _adapter_kwargs(
    fn: Callable[..., Any],
    cfg: dict[str, Any],
    domains: list[str] | None,
    budget: int = 0,
) -> dict[str, Any]:
    """The picker's selection, for adapters that can act on it.

    Most feeds return everything they have and are filtered afterwards. A
    source with a real server-side search — Jobright's `jobTitle` — reaches
    far more of its catalog when it is told what to look for, so pass the
    selection to whichever adapter declares it and leave the rest alone.
    """
    try:
        accepted = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # noqa: BLE001
        return {}
    out: dict[str, Any] = {}
    if "domains" in accepted and domains:
        out["domains"] = list(domains)
    if "roles" in accepted and cfg.get("roles"):
        out["roles"] = list(cfg["roles"])
    if "skip_seen" in accepted:
        # The window is the answer. Hiding ids a previous run ingested made
        # "last 24h" shrink every time it was clicked, until it reported
        # nothing at all — when the honest answer was "these are today's, and
        # you already have most of them".
        out["skip_seen"] = False
    if "time_budget" in accepted and budget:
        # Leave the source room to finish its last request and return, rather
        # than being cancelled with everything still in hand.
        out["time_budget"] = max(15.0, budget - 20.0)
    if "days_ago" in accepted and cfg.get("posted_within_days"):
        # The window, asked of the source rather than applied to its answer.
        # Jobright's search takes a date filter, so "today" can mean one page
        # of today's jobs instead of a week of them cut down afterwards.
        out["days_ago"] = int(cfg["posted_within_days"])
    return out


async def collect_site_listings(
    *,
    site: str,
    window: str = "7d",
    max_jobs: int = 100,
    domains: list[str] | None = None,
    roles: list[str] | None = None,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch from one site/feed or one ATS family, capped and time-filtered."""
    site_meta = resolve_site(site)
    if not site_meta:
        raise ValueError(f"unknown_site:{site}")
    cfg = load_discovery_settings(
        mode="standard",
        window=window,
        max_jobs=max_jobs,
    )
    cfg["site"] = site_meta["id"]
    cfg["site_label"] = site_meta["label"]
    cfg["site_kind"] = site_meta["kind"]
    allowed_domains = normalize_domain_ids(domains)
    cfg["domains"] = allowed_domains
    cfg["roles"] = [r for r in (roles or []) if r]
    # Site fetches try more company boards so one run is useful
    cfg["boards_per_run"] = max(cfg["boards_per_run"], 50 if site_meta["kind"].startswith("ats") else 0)

    cutoff = window_cutoff(window)
    factory = get_session_factory()
    raw: list[RawListing] = []
    ats_this_run: list[dict[str, Any]] = []
    board_ids: list[int] = []

    if on_progress:
        await on_progress(
            {
                "phase": "start",
                "site": site_meta["id"],
                "window": window,
                "domains": allowed_domains,
                "mode": cfg["mode"],
                "done": 0,
                "total": 1,
            }
        )

    kind = site_meta["kind"]
    if kind == "feed":
        fn = ADAPTERS.get(site_meta["id"])
        if not fn:
            raise ValueError(f"no_adapter:{site_meta['id']}")
        if on_progress:
            await on_progress({"phase": "feed", "site": site_meta["id"]})
        # A source that refuses us must say so. Swallowing this reported
        # "Completed - 0 inserted", which reads as "no jobs today" when the
        # truth was that the site returned a bot challenge.
        # Paced sources need longer than a bulk feed; a per-site budget beats
        # one number that is either too short for them or too long for the rest.
        budget = int(site_meta.get("timeout") or 60)
        try:
            async with asyncio.timeout(budget):
                raw = list(await fn(**_adapter_kwargs(fn, cfg, allowed_domains, budget)))
        except TimeoutError:
            cfg["source_error"] = (
                f"{site_meta['label']} did not answer within {budget}s"
            )
            log.warning("site feed %s timed out", site_meta["id"])
            raw = []
        except Exception as exc:  # noqa: BLE001
            cfg["source_error"] = f"{site_meta['label']}: {exc}"
            log.warning("site feed %s failed: %s", site_meta["id"], exc)
            raw = []
    else:
        async with factory() as session:
            await seed_ats_catalog(session, demote_missing=False)
            await session.commit()
            if kind == "ats":
                boards = await boards_for_source(
                    session, source=site_meta["id"], limit=cfg["boards_per_run"]
                )
                # Also include never-checked of this source via rotation helper
                if len(boards) < cfg["boards_per_run"]:
                    more = await next_unchecked_boards(
                        session,
                        limit=cfg["boards_per_run"] - len(boards),
                        source=site_meta["id"],
                    )
                    seen = {b.id for b in boards}
                    boards.extend([b for b in more if b.id not in seen])
            else:
                boards = await next_unchecked_boards(
                    session, limit=cfg["boards_per_run"]
                )
            board_ids = [b.id for b in boards]
        if on_progress:
            await on_progress(
                {
                    "phase": "ats_fetch",
                    "site": site_meta["id"],
                    "boards_total": len(board_ids),
                    "boards_done": 0,
                    "done": 0,
                    "total": max(1, len(board_ids)),
                }
            )
        raw, ats_this_run = await _fetch_boards(
            board_ids, cfg=cfg, on_progress=on_progress
        )

    async with factory() as session:
        ats_meta = await catalog_stats(session)

    # Feeds often lack reliable posted_at — for 24h window keep require flag;
    # for all/7d allow missing dates.
    filtered, by_source, drop_reasons = _filter_listings(
        raw,
        cutoff=cutoff,
        queries=cfg["queries"],
        us_remote_strict=cfg["us_remote_strict"],
        require_posted_at=cfg["require_posted_at"] and kind != "feed",
        allowed_domains=allowed_domains or None,
        allowed_roles=cfg.get("roles") or None,
    )
    diversified = diversify_by_company(filtered)
    insert_limit = cfg["insert_limit"]
    candidates = diversified[: insert_limit * 3]

    out: list[dict[str, Any]] = []
    employer_resolved = 0
    for listing in candidates:
        row = listing_to_dict(listing)
        before = row.get("company")
        row = await enrich_listing_employer(row)
        if row.get("company") != before:
            employer_resolved += 1
        out.append(row)

    meta = {
        "mode": cfg["mode"],
        "site": site_meta["id"],
        "site_label": site_meta["label"],
        "window": window,
        "domains": allowed_domains,
        "roles": cfg.get("roles") or [],
        # Present only when the source refused or timed out; the UI shows it
        # instead of reporting a clean run that found nothing.
        "source_error": cfg.get("source_error"),
        "posted_within_days": cfg["posted_within_days"],
        "discovered_raw": len(raw),
        "after_filters": len(filtered),
        "prefilter_drops": drop_reasons,
        "unique_companies": _count_companies(diversified),
        "by_source": by_source,
        "candidates": len(out),
        "insert_limit": insert_limit,
        "employer_resolved": employer_resolved,
        "ats_this_run": len(ats_this_run),
        "ats_pending": ats_meta.get("pending"),
        "ats_checked": ats_meta.get("checked"),
        "ats_total": ats_meta.get("total"),
        "boards_done": len(board_ids),
        "boards_total": len(board_ids),
        "ats_boards": ats_this_run[:40],
        # Every candidate, not just the slice the window has room to insert —
        # the CSV export is the whole catch. The caller pops this before the
        # rest of `meta` is written to the job's progress: full descriptions
        # for a thousand listings do not belong in a JSON column.
        "found_rows": out,
    }
    log.info(
        "discover[site=%s window=%s]: raw=%s filtered=%s candidates=%s",
        site_meta["id"],
        window,
        len(raw),
        len(filtered),
        len(out),
    )
    return out[:insert_limit], meta
