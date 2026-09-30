"""The Poong extension's ChatGPT panel. See services/chatgpt_bridge."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.enums import JobStatus, PackageStatus
from app.db.models import Job, JobPackage
from app.db.session import get_db
from app.services import chatgpt_bridge
from app.services.employer_conflicts import persona_employers
from app.services.persona import get_active_persona_id, list_personas, load_persona

router = APIRouter(prefix="/api/chatgpt", tags=["chatgpt"])

# Listings nobody should write a resume for.
_CLOSED = (
    JobStatus.BLOCKED.value,
    JobStatus.SKIPPED.value,
    JobStatus.EXPIRED.value,
    JobStatus.REJECTED_ONSITE.value,
)


@router.get("/jobs")
async def list_jobs(
    persona_id: str | None = None,
    show: Literal["need", "all"] = "need",
    q: str = "",
    offset: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Jobs a persona could have a resume written for, newest first, a page at a time.

    "need" is Poong's own Need resume: a usable job description and no resume
    for this persona yet — or one that failed, so it can be tried again. Jobs
    at the persona's own former employers are left out, as they are in Poong.
    """
    pid = persona_id or get_active_persona_id()
    try:
        persona = load_persona(pid)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    stmt = (
        select(Job)
        .options(selectinload(Job.packages))
        .where(func.length(Job.description) >= 50, Job.status.notin_(_CLOSED))
    )
    own = persona_employers(persona)
    if own:
        stmt = stmt.where(Job.normalized_company.notin_(own))
    if show == "need":
        stmt = stmt.where(
            ~Job.packages.any(
                and_(
                    JobPackage.persona_id == pid,
                    JobPackage.status != PackageStatus.FAILED.value,
                )
            )
        )
    term = q.strip()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(or_(Job.company.ilike(like), Job.title.ilike(like)))
    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    # id breaks ties, so a page boundary never shows a job twice or skips one.
    stmt = stmt.order_by(Job.seq_no.desc(), Job.id.desc()).offset(offset).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    jobs = []
    for job in rows:
        mine = next((p for p in job.packages if p.persona_id == pid), None)
        jobs.append(
            {
                "id": job.id,
                "seq_no": job.seq_no,
                "company": job.company,
                "title": job.title,
                "location": job.location,
                "state": mine.status if mine else "need_resume",
                "note": (mine.notes or "")[:200] if mine else "",
            }
        )
    return {
        "persona_id": pid,
        "personas": [{"id": p["id"], "label": p.get("label") or p["id"]} for p in list_personas()],
        # Every match; `jobs` is the page of them starting at `offset`.
        "total": total,
        "offset": offset,
        "limit": limit,
        "jobs": jobs,
    }


class StartBody(BaseModel):
    job_id: int
    persona_id: str


class ReplyBody(BaseModel):
    step: int
    text: str


class FailBody(BaseModel):
    step: int
    error: str


def _gone(exc: chatgpt_bridge.ChatGPTBridgeError) -> HTTPException:
    return HTTPException(404, detail=str(exc))


@router.post("/sessions")
async def start_session(body: StartBody, db: AsyncSession = Depends(get_db)):
    """Start one job; answers with its first prompt."""
    job = await db.get(Job, body.job_id)
    if job is None:
        raise HTTPException(404, detail="job_missing")
    if not (job.description and len(job.description) >= 50):
        raise HTTPException(400, detail="jd_too_short")
    try:
        load_persona(body.persona_id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return await chatgpt_bridge.start(body.job_id, body.persona_id)


@router.post("/sessions/{session_id}/reply")
async def post_reply(session_id: str, body: ReplyBody):
    """ChatGPT's answer; answers with the next prompt, or the job's outcome."""
    try:
        return await chatgpt_bridge.reply(session_id, body.step, body.text)
    except chatgpt_bridge.ChatGPTBridgeError as exc:
        raise _gone(exc) from exc


@router.post("/sessions/{session_id}/fail")
async def post_fail(session_id: str, body: FailBody):
    """The tab could not get an answer; the job fails with the reason given."""
    try:
        return await chatgpt_bridge.fail(session_id, body.step, body.error[:500])
    except chatgpt_bridge.ChatGPTBridgeError as exc:
        raise _gone(exc) from exc


@router.get("/sessions/{session_id}/next")
async def get_next(session_id: str):
    """The next prompt or the outcome, for when a reply's answer was still pending."""
    try:
        return await chatgpt_bridge.next_step(session_id)
    except chatgpt_bridge.ChatGPTBridgeError as exc:
        raise _gone(exc) from exc


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str):
    return {"cancelled": chatgpt_bridge.cancel(session_id)}


@router.get("/status")
async def status():
    return chatgpt_bridge.status()
