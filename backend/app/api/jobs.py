from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import JobStatus, PackageStatus, can_transition
from app.db.models import Application, Job, JobPackage
from app.db.session import get_db
from app.services.apply import mark_job_applied, stop_apply_for_job
from app.services.apply_session import apply_sessions
from app.services.deep_scan import deep_scan_job
from app.services.folder_writer import remove_all_persona_outputs, sync_job_folder
from app.services.manual_ingest import parse_upload
from app.services.persona import get_active_persona_id
from app.services.pipeline import process_listing
from app.services.renumber import renumber_all_jobs
from app.services.tailor import tailor_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    company: str
    title: str
    url: str
    location: str | None = None
    external_id: str | None = None
    source: str = "manual"
    description: str | None = None
    staffing_firm: str | None = None
    persona_id: str | None = None


class StatusPatch(BaseModel):
    status: str
    persona_id: str | None = None
    package_id: int | None = None


class RangeDelete(BaseModel):
    seq_from: int = Field(..., ge=1)
    seq_to: int = Field(..., ge=1)
    remove_folders: bool = True


class IdsDelete(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=2000)
    remove_folders: bool = True


def _package_dict(pkg: JobPackage) -> dict[str, Any]:
    return {
        "id": pkg.id,
        "persona_id": pkg.persona_id,
        "status": pkg.status,
        "folder_path": pkg.folder_path,
        "notes": pkg.notes,
        "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
        "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else None,
    }


async def _get_job_with_packages(db: AsyncSession, job_id: int) -> Job | None:
    return (
        await db.execute(
            select(Job).options(selectinload(Job.packages)).where(Job.id == job_id)
        )
    ).scalar_one_or_none()


async def _purge_jobs(
    db: AsyncSession,
    jobs: list[Job],
    *,
    remove_folders: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    deleted: list[dict[str, Any]] = []
    folders_removed = 0
    for job in jobs:
        pkgs = (
            await db.execute(select(JobPackage).where(JobPackage.job_id == job.id))
        ).scalars().all()
        deleted.append(
            {
                "id": job.id,
                "seq_no": job.seq_no,
                "company": job.company,
                "title": job.title,
                "folder_path": job.folder_path,
                "packages": [_package_dict(p) for p in pkgs],
            }
        )
        if remove_folders:
            if remove_all_persona_outputs(
                seq_no=job.seq_no,
                company=job.company,
                folder_paths=[job.folder_path, *[p.folder_path for p in pkgs]],
                persona_ids=[job.persona_id, *[p.persona_id for p in pkgs]],
            ):
                folders_removed += 1
        await db.execute(
            update(Application).where(Application.job_id == job.id).values(job_id=None)
        )
        await apply_sessions.end_session(job.id)
        await db.delete(job)
    return deleted, folders_removed


_PACKAGE_DERIVED_LISTING = {
    JobStatus.RESUME_READY.value,
    JobStatus.APPLYING.value,
    JobStatus.APPLIED.value,
}


def _listing_pipeline_status(job: Job) -> str:
    """Listing status is pipeline-only; never treat one persona apply as global done."""
    terminal = {
        JobStatus.BLOCKED.value,
        JobStatus.SKIPPED.value,
        JobStatus.EXPIRED.value,
        JobStatus.REJECTED_ONSITE.value,
        JobStatus.SCAN_FAILED.value,
        JobStatus.NEEDS_MANUAL.value,
    }
    if job.status in terminal:
        return job.status
    if job.status in _PACKAGE_DERIVED_LISTING:
        return (
            JobStatus.JD_READY.value
            if job.description and len(job.description) >= 50
            else JobStatus.NEW.value
        )
    return job.status


def _job_dict(job: Job, *, profile: str | None = None) -> dict[str, Any]:
    packages = [_package_dict(p) for p in getattr(job, "packages", None) or []]
    packages.sort(key=lambda p: p.get("persona_id") or "")
    my_package = next((p for p in packages if p["persona_id"] == profile), None)
    return {
        "id": job.id,
        "seq_no": job.seq_no,
        "company": job.company,
        "title": job.title,
        "url": job.url,
        "apply_url": job.apply_url,
        "location": job.location,
        "source": job.source,
        "external_id": job.external_id,
        "description": job.description,
        "status": _listing_pipeline_status(job),
        "folder_path": job.folder_path,
        "persona_id": job.persona_id,
        "staffing_firm": job.staffing_firm,
        "jd_fetched": job.jd_fetched,
        "notes": (my_package or {}).get("notes") or job.notes,
        "packages": packages,
        "package_count": len(packages),
        "my_status": my_package["status"] if my_package else "need_resume",
        "my_package_id": my_package["id"] if my_package else None,
        "applied_count": sum(1 for p in packages if p["status"] == PackageStatus.APPLIED.value),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.get("")
async def list_jobs(
    status: str | None = None,
    q: str | None = None,
    seq_from: int | None = None,
    seq_to: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    offset: int | None = Query(None, ge=0),
    limit: int | None = Query(None, ge=1, le=200),
    persona_id: str | None = None,
    profile: str | None = None,
    profile_status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List jobs with pagination. No hard cap on total rows — page through all matches."""
    from sqlalchemy import and_, or_
    from app.services.persona import list_personas

    filters = []
    if persona_id:
        filters.append(Job.persona_id == persona_id)
    if profile and profile_status:
        if profile_status == "need_resume":
            filters.append(~Job.packages.any(JobPackage.persona_id == profile))
        else:
            filters.append(
                Job.packages.any(
                    and_(
                        JobPackage.persona_id == profile,
                        JobPackage.status == profile_status,
                    )
                )
            )
    if status:
        if status == JobStatus.JD_READY.value:
            filters.append(
                or_(
                    Job.status == JobStatus.JD_READY.value,
                    Job.status.in_(list(_PACKAGE_DERIVED_LISTING)),
                )
            )
        else:
            filters.append(Job.status == status)
    if seq_from is not None:
        filters.append(Job.seq_no >= seq_from)
    if seq_to is not None:
        filters.append(Job.seq_no <= seq_to)
    if q:
        term = q.strip()
        if term:
            like = f"%{term}%"
            normalized_term = term.casefold().replace("_", " ")
            matching_profiles = [
                persona["id"]
                for persona in list_personas()
                if normalized_term
                in f"{persona['id']} {persona.get('display_name') or ''}".casefold().replace(
                    "_", " "
                )
            ]
            search_clauses = [
                Job.company.ilike(like),
                Job.title.ilike(like),
                Job.packages.any(JobPackage.persona_id.ilike(like)),
                Job.persona_id.ilike(like),
            ]
            if matching_profiles:
                search_clauses.extend(
                    [
                        Job.packages.any(JobPackage.persona_id.in_(matching_profiles)),
                        Job.persona_id.in_(matching_profiles),
                    ]
                )
            filters.append(or_(*search_clauses))

    count_stmt = select(func.count(Job.id))
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = int((await db.execute(count_stmt)).scalar_one() or 0)

    size = limit or page_size
    start = offset if offset is not None else (page - 1) * size
    if start < 0:
        start = 0
    pages = max(1, (total + size - 1) // size) if total else 1
    current_page = (start // size) + 1 if size else 1

    stmt = (
        select(Job)
        .options(selectinload(Job.packages))
        .order_by(Job.seq_no.asc(), Job.id.desc())
    )
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.offset(start).limit(size)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "jobs": [_job_dict(j, profile=profile) for j in rows],
        "total": total,
        "page": current_page,
        "page_size": size,
        "pages": pages,
        "offset": start,
        "has_more": start + len(rows) < total,
    }


@router.get("/meta/stats")
async def job_stats(db: AsyncSession = Depends(get_db)):
    from app.services.persona import list_personas

    rows = (
        await db.execute(select(Job.status, func.count()).group_by(Job.status))
    ).all()
    by_status = {s: c for s, c in rows}
    total = sum(by_status.values())
    pkg_rows = (
        await db.execute(
            select(JobPackage.persona_id, JobPackage.status, func.count()).group_by(
                JobPackage.persona_id, JobPackage.status
            )
        )
    ).all()
    by_profile: dict[str, dict[str, int]] = {}
    for pid, st, c in pkg_rows:
        by_profile.setdefault(pid, {})[st] = c
    for persona in list_personas():
        pid = persona["id"]
        statuses = by_profile.setdefault(pid, {})
        packaged = sum(statuses.values())
        statuses["need_resume"] = max(0, total - packaged)
    return {"total": total, "by_status": by_status, "by_profile": by_profile}


@router.post("")
async def create_job(body: JobCreate, db: AsyncSession = Depends(get_db)):
    result = await process_listing(
        db,
        body.model_dump(exclude_none=True),
        persona_id=body.persona_id or get_active_persona_id(),
        skip_content_gates=True,
    )
    await db.commit()
    if not result.ok:
        raise HTTPException(400, detail=result.reason or "insert_failed")
    await db.refresh(result.job)
    return _job_dict(result.job)


@router.post("/import")
async def import_jobs(
    file: UploadFile = File(...),
    persona_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    try:
        listings, meta = parse_upload(file.filename or "jobs.json", content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail=f"parse_error: {exc}") from exc

    if not listings:
        raise HTTPException(
            400,
            detail={
                "error": "no_jobs_parsed",
                "message": (
                    "No rows mapped. CSV needs company + title + url columns "
                    "(aliases: company_name, job_title, job_link / apply_url, …)."
                ),
                "parse": meta,
            },
        )

    pid = persona_id or get_active_persona_id()
    inserted = []
    jd_ready = []
    rejected = []
    skipped = []
    for listing in listings:
        # skip_content_gates=True for rows WITHOUT JD (deep-scan later).
        # process_listing still runs gates when description is present.
        result = await process_listing(
            db, listing, persona_id=pid, skip_content_gates=True, insert_rejects=True
        )
        if result.job:
            inserted.append(result.job.id)
            st = result.status or result.job.status
            if st == "jd_ready":
                jd_ready.append(result.job.id)
            elif st in {"rejected_onsite", "skipped", "needs_manual", "blocked"}:
                rejected.append({"id": result.job.id, "company": result.job.company, "reason": result.reason, "status": st})
        else:
            skipped.append({"company": listing.get("company"), "reason": result.reason})
    await db.commit()
    return {
        "inserted": len(inserted),
        "jd_ready": len(jd_ready),
        "rejected": rejected,
        "skipped": skipped,
        "ids": inserted,
        "parse": meta,
        "message": (
            f"Inserted {len(inserted)} "
            f"({len(jd_ready)} jd_ready from embedded description, "
            f"{len(rejected)} rejected by gates, "
            f"{len(skipped)} skipped)."
        ),
    }


@router.delete("/all")
async def wipe_jobs(
    keep_applied_history: bool = True,
    remove_folders: bool = True,
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(Job))).scalars().all()
    deleted, folders_removed = await _purge_jobs(db, list(rows), remove_folders=remove_folders)
    if not keep_applied_history:
        await db.execute(delete(Application))
    renumber = await renumber_all_jobs(db)
    await db.commit()
    return {
        "ok": True,
        "deleted": len(deleted),
        "folders_removed": folders_removed,
        "renumber": renumber,
    }


@router.post("/renumber")
async def renumber_jobs(db: AsyncSession = Depends(get_db)):
    """Force contiguous seq_no 1..N and rename all persona resume folders to match."""
    renumber = await renumber_all_jobs(db)
    await db.commit()
    return renumber


@router.post("/delete-range")
async def delete_jobs_range(body: RangeDelete, db: AsyncSession = Depends(get_db)):
    if body.seq_to < body.seq_from:
        raise HTTPException(400, detail="seq_to must be >= seq_from")
    q = (
        select(Job)
        .where(Job.seq_no >= body.seq_from, Job.seq_no <= body.seq_to)
        .order_by(Job.seq_no.asc())
    )
    rows = list((await db.execute(q)).scalars().all())
    deleted, folders_removed = await _purge_jobs(db, rows, remove_folders=body.remove_folders)
    renumber = await renumber_all_jobs(db)
    await db.commit()
    return {
        "ok": True,
        "deleted": len(deleted),
        "folders_removed": folders_removed,
        "seq_from": body.seq_from,
        "seq_to": body.seq_to,
        "jobs": deleted,
        "renumber": renumber,
    }


@router.post("/delete-ids")
async def delete_jobs_ids(body: IdsDelete, db: AsyncSession = Depends(get_db)):
    ids = sorted(set(body.ids))
    if not ids:
        raise HTTPException(400, detail="no_ids")
    q = select(Job).where(Job.id.in_(ids)).order_by(Job.seq_no.asc())
    rows = list((await db.execute(q)).scalars().all())
    deleted, folders_removed = await _purge_jobs(db, rows, remove_folders=body.remove_folders)
    renumber = await renumber_all_jobs(db)
    await db.commit()
    return {
        "ok": True,
        "deleted": len(deleted),
        "folders_removed": folders_removed,
        "requested": len(ids),
        "jobs": deleted,
        "renumber": renumber,
    }


@router.get("/{job_id}")
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await _get_job_with_packages(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    return _job_dict(job)


@router.delete("/{job_id}")
async def delete_job(
    job_id: int,
    remove_folders: bool = True,
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    deleted, folders_removed = await _purge_jobs(db, [job], remove_folders=remove_folders)
    renumber = await renumber_all_jobs(db)
    await db.commit()
    return {
        "ok": True,
        "deleted": 1,
        "folders_removed": folders_removed,
        "job": deleted[0],
        "renumber": renumber,
    }


@router.patch("/{job_id}/status")
async def patch_status(job_id: int, body: StatusPatch, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    target = body.status
    try:
        JobStatus(target)
    except ValueError as exc:
        raise HTTPException(400, detail="invalid_status") from exc

    if target == JobStatus.APPLIED.value:
        try:
            job = await mark_job_applied(
                db,
                job,
                persona_id=body.persona_id,
                package_id=body.package_id,
            )
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        return _job_dict(await _get_job_with_packages(db, job.id) or job)
    if target in {JobStatus.RESUME_READY.value, JobStatus.APPLYING.value}:
        raise HTTPException(400, detail="package_status_requires_profile")

    if not can_transition(job.status, target):
        raise HTTPException(400, detail=f"cannot_transition:{job.status}->{target}")
    job.status = target
    if target == JobStatus.SKIPPED.value:
        await stop_apply_for_job(db, job, revert_packages=True)
    if job.seq_no:
        sync_job_folder(
            persona_id=job.persona_id,
            seq_no=job.seq_no,
            company=job.company,
            title=job.title,
            url=job.url,
            location=job.location,
            status=job.status,
            description=job.description,
            job_id=job.id,
        )
    await db.commit()
    return _job_dict(await _get_job_with_packages(db, job.id) or job)


@router.post("/{job_id}/block-company")
async def block_company(job_id: int, db: AsyncSession = Depends(get_db)):
    from app.db.models import Company

    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    result = await db.execute(
        select(Company).where(Company.normalized_name == job.normalized_company)
    )
    company = result.scalar_one_or_none()
    if company:
        company.blocked = True
    job.status = JobStatus.BLOCKED.value
    await db.commit()
    await apply_sessions.end_session(job.id)
    return _job_dict(job)


@router.post("/{job_id}/deep-scan")
async def deep_scan_one(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    result = await deep_scan_job(db, job, force=True)
    await db.commit()
    return result


class TailorBody(BaseModel):
    persona_id: str | None = None


@router.post("/{job_id}/tailor")
async def tailor_one(
    job_id: int,
    body: TailorBody | None = None,
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    try:
        persona_id = (body.persona_id if body else None) or None
        result = await tailor_job(db, job, persona_id=persona_id)
        await db.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/{job_id}/refresh-folder")
async def refresh_folder(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    if not job.seq_no:
        raise HTTPException(400, detail="no_seq")
    folder = sync_job_folder(
        persona_id=job.persona_id,
        seq_no=job.seq_no,
        company=job.company,
        title=job.title,
        url=job.url,
        location=job.location,
        status=job.status,
        description=job.description,
        job_id=job.id,
    )
    job.folder_path = str(folder)
    await db.commit()
    return {"folder": str(folder)}


@router.get("/{job_id}/questions")
async def job_questions(job_id: int, db: AsyncSession = Depends(get_db)):
    import re

    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    qs = []
    for m in re.finditer(r"(?m)^\s*(?:\d+[.)]\s*)?([A-Z][^?\n]{8,120}\?)\s*$", job.description or ""):
        qs.append(m.group(1).strip())
    return {"questions": qs}
