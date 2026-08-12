from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RejectedJob
from app.db.session import get_db

router = APIRouter(prefix="/api/rejected", tags=["rejected"])


@router.get("")
async def list_rejected(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    q = select(RejectedJob).order_by(RejectedJob.id.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return {
        "rejected": [
            {
                "id": r.id,
                "company": r.company,
                "title": r.title,
                "url": r.url,
                "reason": r.reason,
                "stage": r.stage,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
