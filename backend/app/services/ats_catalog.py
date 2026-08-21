"""Rotate through real company ATS boards so Fetch new jobs is not stuck on the same names."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import yaml
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AtsBoard, utcnow
from app.services.config_loader import load_job_sites

log = logging.getLogger("poong.ats")

SOURCES = ("greenhouse", "lever", "ashby", "workable")


def _load_seed() -> list[tuple[str, str]]:
    cfg = load_job_sites()
    extra_path = get_settings().config_dir / "ats_boards.yml"
    extra: dict[str, Any] = {}
    if extra_path.exists():
        extra = yaml.safe_load(extra_path.read_text(encoding="utf-8")) or {}
        if not isinstance(extra, dict):
            extra = {}
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source, key in (
        ("greenhouse", "greenhouse_boards"),
        ("lever", "lever_boards"),
        ("ashby", "ashby_boards"),
    ):
        tokens = list(cfg.get(key) or []) + list(extra.get(source) or extra.get(key) or [])
        for raw in tokens:
            token = str(raw or "").strip().lower()
            if not token:
                continue
            pair = (source, token)
            if pair in seen:
                continue
            seen.add(pair)
            out.append(pair)
    return out


async def seed_ats_catalog(session: AsyncSession, *, demote_missing: bool = False) -> int:
    existing = {
        (s, t)
        for s, t in (
            await session.execute(select(AtsBoard.source, AtsBoard.token))
        ).all()
    }
    added = 0
    for source, token in _load_seed():
        if (source, token) in existing:
            continue
        session.add(AtsBoard(source=source, token=token, status="pending"))
        existing.add((source, token))
        added += 1
    if added:
        log.info("ats catalog seeded +%s boards", added)
    if demote_missing:
        await demote_404_boards(session)
    return added


async def demote_404_boards(session: AsyncSession) -> int:
    """Mark historical 404 failures as missing so discover prefers real pending boards."""
    result = await session.execute(
        update(AtsBoard)
        .where(
            AtsBoard.status == "failed",
            AtsBoard.error.is_not(None),
            AtsBoard.error.contains("404"),
        )
        .values(status="missing")
    )
    n = int(result.rowcount or 0)
    if n:
        log.info("ats catalog demoted %s failed-404 boards → missing", n)
    return n


async def add_boards(
    session: AsyncSession,
    boards: list[tuple[str, str] | tuple[str, str, str | None]],
) -> int:
    """Insert (source, token[, company_name]) rows that are not already in the catalog."""
    existing = {
        (s, t)
        for s, t in (
            await session.execute(select(AtsBoard.source, AtsBoard.token))
        ).all()
    }
    added = 0
    for item in boards:
        source = str(item[0] or "").strip().lower()
        token = str(item[1] or "").strip().lower()
        company_name = str(item[2]).strip() if len(item) > 2 and item[2] else None
        if source not in SOURCES or not token:
            continue
        pair = (source, token)
        if pair in existing:
            continue
        session.add(
            AtsBoard(
                source=source,
                token=token,
                company_name=(company_name[:255] if company_name else None),
                status="pending",
            )
        )
        existing.add(pair)
        added += 1
    if added:
        log.info("ats catalog +%s boards from discovery", added)
    return added


async def next_unchecked_boards(
    session: AsyncSession,
    *,
    limit: int = 30,
    source: str | None = None,
) -> list[AtsBoard]:
    """Prefer never-checked companies, then the oldest previously checked ones."""
    stale_ok = utcnow() - timedelta(days=14)
    stale_fail = utcnow() - timedelta(days=30)
    stale_missing = utcnow() - timedelta(days=180)
    filters = [
        or_(
            AtsBoard.last_checked_at.is_(None),
            AtsBoard.status == "pending",
            (AtsBoard.status.in_(("ok", "empty"))) & (AtsBoard.last_checked_at <= stale_ok),
            (AtsBoard.status == "failed") & (AtsBoard.last_checked_at <= stale_fail),
            (AtsBoard.status == "missing") & (AtsBoard.last_checked_at <= stale_missing),
        )
    ]
    source_norm = (source or "").strip().lower()
    if source_norm:
        filters.append(AtsBoard.source == source_norm)
    q = (
        select(AtsBoard)
        .where(*filters)
        .order_by(
            case(
                (AtsBoard.last_checked_at.is_(None), 0),
                (AtsBoard.status == "pending", 0),
                (AtsBoard.status.in_(("ok", "empty")), 1),
                (AtsBoard.status == "failed", 2),
                else_=3,
            ),
            AtsBoard.last_checked_at.asc(),
            AtsBoard.id.asc(),
        )
        .limit(max(1, limit))
    )
    return list((await session.execute(q)).scalars().all())


async def boards_for_source(
    session: AsyncSession,
    *,
    source: str,
    limit: int = 80,
) -> list[AtsBoard]:
    """Site fetch: prefer live ok boards, then pending, for one ATS."""
    source_norm = (source or "").strip().lower()
    q = (
        select(AtsBoard)
        .where(
            AtsBoard.source == source_norm,
            AtsBoard.status.in_(("ok", "pending", "empty")),
        )
        .order_by(
            case(
                (AtsBoard.status == "ok", 0),
                (AtsBoard.status == "pending", 1),
                else_=2,
            ),
            case(
                (AtsBoard.last_checked_at.is_(None), 0),
                else_=1,
            ),
            AtsBoard.last_checked_at.asc(),
            AtsBoard.id.asc(),
        )
        .limit(max(1, limit))
    )
    return list((await session.execute(q)).scalars().all())


async def mark_board(
    session: AsyncSession,
    board: AtsBoard,
    *,
    jobs_found: int,
    company_name: str | None = None,
    error: str | None = None,
) -> None:
    board.last_checked_at = utcnow()
    board.jobs_found = int(jobs_found)
    if company_name:
        board.company_name = company_name[:255]
    if error:
        # Permanent miss — don't burn future discover slots on known-bad slugs
        if "404" in error:
            board.status = "missing"
        else:
            board.status = "failed"
        board.error = error[:512]
    elif jobs_found > 0:
        board.status = "ok"
        board.error = None
    else:
        board.status = "empty"
        board.error = None


async def catalog_stats(session: AsyncSession) -> dict[str, Any]:
    total = int((await session.execute(select(func.count(AtsBoard.id)))).scalar_one() or 0)
    pending = int(
        (
            await session.execute(
                select(func.count(AtsBoard.id)).where(
                    or_(AtsBoard.last_checked_at.is_(None), AtsBoard.status == "pending")
                )
            )
        ).scalar_one()
        or 0
    )
    by_status = {
        s: c
        for s, c in (
            await session.execute(select(AtsBoard.status, func.count()).group_by(AtsBoard.status))
        ).all()
    }
    with_jobs = int(
        (
            await session.execute(
                select(func.count(AtsBoard.id)).where(AtsBoard.jobs_found > 0)
            )
        ).scalar_one()
        or 0
    )
    recent = (
        await session.execute(
            select(AtsBoard)
            .where(AtsBoard.last_checked_at.is_not(None))
            .order_by(AtsBoard.last_checked_at.desc())
            .limit(12)
        )
    ).scalars().all()
    return {
        "total": total,
        "pending": pending,
        "checked": max(0, total - pending),
        "with_jobs": with_jobs,
        "by_status": by_status,
        "recent": [
            {
                "source": b.source,
                "token": b.token,
                "company_name": b.company_name,
                "status": b.status,
                "jobs_found": b.jobs_found,
                "last_checked_at": b.last_checked_at.isoformat() if b.last_checked_at else None,
            }
            for b in recent
        ],
    }


def _board_dict(b: AtsBoard) -> dict[str, Any]:
    return {
        "id": b.id,
        "source": b.source,
        "token": b.token,
        "company_name": b.company_name,
        "status": b.status,
        "jobs_found": b.jobs_found,
        "error": b.error,
        "last_checked_at": b.last_checked_at.isoformat() if b.last_checked_at else None,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


async def list_boards(
    session: AsyncSession,
    *,
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated ATS company catalog for the UI."""
    filters = []
    status_norm = (status or "").strip().lower()
    if status_norm and status_norm != "all":
        if status_norm == "searched":
            filters.append(AtsBoard.status.in_(("ok", "empty", "failed", "missing")))
            filters.append(AtsBoard.last_checked_at.is_not(None))
        elif status_norm == "waiting":
            filters.append(
                or_(AtsBoard.last_checked_at.is_(None), AtsBoard.status == "pending")
            )
        else:
            filters.append(AtsBoard.status == status_norm)
    source_norm = (source or "").strip().lower()
    if source_norm and source_norm != "all":
        filters.append(AtsBoard.source == source_norm)
    query_text = (q or "").strip()
    if query_text:
        like = f"%{query_text}%"
        filters.append(
            or_(
                AtsBoard.token.ilike(like),
                AtsBoard.company_name.ilike(like),
            )
        )

    count_q = select(func.count(AtsBoard.id))
    list_q = select(AtsBoard)
    if filters:
        count_q = count_q.where(*filters)
        list_q = list_q.where(*filters)

    total = int((await session.execute(count_q)).scalar_one() or 0)
    rows = (
        await session.execute(
            list_q.order_by(
                case(
                    (AtsBoard.last_checked_at.is_(None), 1),
                    else_=0,
                ),
                AtsBoard.last_checked_at.desc(),
                AtsBoard.id.desc(),
            )
            .offset(max(0, offset))
            .limit(max(1, min(500, limit)))
        )
    ).scalars().all()

    return {
        "total": total,
        "limit": max(1, min(500, limit)),
        "offset": max(0, offset),
        "boards": [_board_dict(b) for b in rows],
    }
