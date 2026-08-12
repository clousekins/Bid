from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.enums import BackgroundJobKind, JobStatus, PackageStatus
from app.db.models import Job, JobPackage
from app.db.session import get_db
from app.services import bg_jobs
from app.services.persona import get_active_persona_id

router = APIRouter(prefix="/api/sync", tags=["sync"])


class RangeBody(BaseModel):
    persona_id: str | None = None
    seq_from: int | None = 1
    seq_to: int | None = 50
    limit: int = 50
    job_ids: list[int] | None = None


@router.post("/discover")
async def discover_disabled():
    raise HTTPException(410, detail="Board discovery disabled. Use manual JSON/CSV ingest.")


@router.post("/deep-scan")
async def sync_deep_scan(body: RangeBody):
    pid = body.persona_id or get_active_persona_id()
    ids = body.job_ids or None
    if ids is not None and not ids:
        raise HTTPException(400, detail="job_ids empty")
    if ids is None and (body.seq_from is None or body.seq_to is None):
        raise HTTPException(400, detail="seq_from/seq_to or job_ids required")
    row = await bg_jobs.enqueue(
        kind=BackgroundJobKind.DEEP_SCAN,
        persona_id=pid,
        seq_from=body.seq_from,
        seq_to=body.seq_to,
        job_ids=ids,
    )
    return {"job_id": row.id, "kind": row.kind, "status": row.status}


@router.post("/tailor")
async def sync_tailor(body: RangeBody):
    gen_pid = body.persona_id or get_active_persona_id()
    ids = body.job_ids or None
    if ids is not None and not ids:
        raise HTTPException(400, detail="job_ids empty")
    if ids is None and (body.seq_from is None or body.seq_to is None):
        raise HTTPException(400, detail="seq_from/seq_to or job_ids required")
    # Job rows are usually under the active import persona; generation persona is separate
    filter_persona = get_active_persona_id()
    row = await bg_jobs.enqueue(
        kind=BackgroundJobKind.TAILOR,
        persona_id=filter_persona,
        seq_from=body.seq_from,
        seq_to=body.seq_to,
        job_ids=ids,
        generate_as=gen_pid,
    )
    return {"job_id": row.id, "kind": row.kind, "status": row.status, "persona_id": gen_pid}


@router.post("/apply-preview")
async def apply_preview(body: RangeBody, db: AsyncSession = Depends(get_db)):
    pid = body.persona_id or get_active_persona_id()
    q = (
        select(Job)
        .options(selectinload(Job.packages))
        .join(
            JobPackage,
            (JobPackage.job_id == Job.id)
            & (JobPackage.persona_id == pid)
            & (JobPackage.status == PackageStatus.RESUME_READY.value)
            & (JobPackage.folder_path.is_not(None)),
        )
        .where(
            Job.seq_no >= body.seq_from,
            Job.seq_no <= body.seq_to,
            Job.description.is_not(None),
            Job.status.not_in(
                [
                    JobStatus.BLOCKED.value,
                    JobStatus.SKIPPED.value,
                    JobStatus.EXPIRED.value,
                    JobStatus.REJECTED_ONSITE.value,
                ]
            ),
        )
        .order_by(Job.seq_no.asc())
        .limit(body.limit)
    )
    rows = (await db.execute(q)).scalars().all()
    return {
        "jobs": [
            {
                "id": j.id,
                "seq_no": j.seq_no,
                "company": j.company,
                "title": j.title,
                "url": j.url,
                "status": j.status,
                "packages": [
                    {
                        "id": p.id,
                        "persona_id": p.persona_id,
                        "status": p.status,
                        "folder_path": p.folder_path,
                    }
                    for p in j.packages
                ],
            }
            for j in rows
        ]
    }
