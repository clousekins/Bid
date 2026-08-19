"""Rotate through real company ATS boards so Fetch new jobs is not stuck on the same names."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import yaml
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AtsBoard, utcnow
from app.services.config_loader import load_job_sites

log = logging.getLogger("poong.ats")

SOURCES = ("greenhouse", "lever", "ashby")


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


async def seed_ats_catalog(session: AsyncSession) -> int:
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
    return added


async def next_unchecked_boards(session: AsyncSession, *, limit: int = 30) -> list[AtsBoard]:
    """Prefer never-checked companies, then the oldest previously checked ones."""
    stale_ok = utcnow() - timedelta(days=14)
    stale_fail = utcnow() - timedelta(days=30)
    q = (
        select(AtsBoard)
        .where(
            or_(
                AtsBoard.last_checked_at.is_(None),
                AtsBoard.status == "pending",
                (AtsBoard.status.in_(("ok", "empty"))) & (AtsBoard.last_checked_at <= stale_ok),
                (AtsBoard.status == "failed") & (AtsBoard.last_checked_at <= stale_fail),
            )
        )
        .order_by(
            case((AtsBoard.last_checked_at.is_(None), 0), else_=1),
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
