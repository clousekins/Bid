from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from app.services import bg_jobs

router = APIRouter(prefix="/api/jobs-bg", tags=["jobs-bg"])


@router.get("/{job_id}")
async def get_bg_job(job_id: int):
    row = await bg_jobs.get_job(job_id)
    if not row:
        raise HTTPException(404, detail="not_found")
    progress = None
    if row.progress:
        try:
            progress = json.loads(row.progress)
        except json.JSONDecodeError:
            progress = row.progress
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "persona_id": row.persona_id,
        "seq_from": row.seq_from,
        "seq_to": row.seq_to,
        "progress": progress,
        "error": row.error,
        "cancel_requested": row.cancel_requested,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.post("/{job_id}/cancel")
async def cancel_bg_job(job_id: int):
    ok = await bg_jobs.request_cancel(job_id)
    if not ok:
        raise HTTPException(404, detail="not_found")
    return {"ok": True}
