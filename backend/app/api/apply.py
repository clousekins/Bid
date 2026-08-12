from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job
from app.db.session import get_db
from app.services.apply import start_apply
from app.services.apply_session import apply_sessions

router = APIRouter(prefix="/api/apply", tags=["apply"])


class StartBody(BaseModel):
    ui_opened_tab: bool = True
    persona_id: str | None = None
    package_id: int | None = None


@router.post("/{job_id}/start")
async def apply_start(job_id: int, body: StartBody | None = None, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    body = body or StartBody()
    try:
        sess = await start_apply(
            db,
            job,
            ui_opened_tab=body.ui_opened_tab,
            persona_id=body.persona_id,
            package_id=body.package_id,
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return sess.to_dict()


@router.get("/{job_id}/status")
async def apply_status(job_id: int):
    sess = apply_sessions.get(job_id)
    if not sess:
        return {"job_id": job_id, "status": "none", "logs": []}
    return sess.to_dict()


@router.post("/{job_id}/resume")
async def apply_resume(job_id: int):
    ok = await apply_sessions.resume(job_id)
    if not ok:
        raise HTTPException(404, detail="no_session")
    return apply_sessions.get(job_id).to_dict()  # type: ignore[union-attr]


@router.post("/{job_id}/confirm")
async def apply_confirm(job_id: int):
    ok = await apply_sessions.confirm(job_id)
    if not ok:
        raise HTTPException(404, detail="no_session")
    return apply_sessions.get(job_id).to_dict()  # type: ignore[union-attr]


@router.post("/{job_id}/cancel")
async def apply_cancel(job_id: int):
    ok = await apply_sessions.cancel(job_id)
    if not ok:
        raise HTTPException(404, detail="no_session")
    return {"ok": True}
