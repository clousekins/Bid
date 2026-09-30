from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.enums import (
    RESPONSE_OUTCOMES,
    ApplicationOutcome,
    JobStatus,
    PackageStatus,
    can_transition,
)
from app.db.models import Application, Job, JobPackage, utcnow
from app.db.session import get_db
from app.services.apply import mark_job_applied, stop_apply_for_job
from app.services.artifacts import KINDS, folder_is_allowed, resolve_artifacts, resolve_kind
from app.services.apply_session import apply_sessions
from app.services.deep_scan import deep_scan_job
from app.services.job_country import country_name
from app.services.folder_writer import (
    job_folder_name,
    remove_all_persona_outputs,
    sync_job_folder,
)
from app.services.manual_ingest import parse_upload
from app.services.ats_score import score_resume, technical_feasibility
from app.services.employer_conflicts import conflict_reason, persona_employers
from app.services import runtime_settings
from app.services.persona import get_active_persona_id, load_persona
from app.services.pipeline import (
    _get_or_create_company,
    forget_job_markers,
    process_listing,
)
from app.services.renumber import renumber_all_jobs
from app.services.tailor import tailor_job
from app.validators.dedupe import normalize_company

log = logging.getLogger("poong.jobs")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Sentinel so callers can pass persona=None to mean "no fit score".
_UNSET = object()


class JobCreate(BaseModel):
    company: str
    title: str
    url: str
    # Where the application actually lives. Aggregator listings point elsewhere
    # than the posting page; everything else leaves this unset and the pipeline
    # falls back to url.
    apply_url: str | None = None
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


class OutcomePatch(BaseModel):
    outcome: str
    persona_id: str | None = None
    package_id: int | None = None
    notes: str | None = None


class JobFieldsPatch(BaseModel):
    company: str | None = None
    title: str | None = None
    url: str | None = None
    location: str | None = None
    apply_url: str | None = None


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
        "applied_at": pkg.applied_at.isoformat() if pkg.applied_at else None,
        "outcome": pkg.outcome or ApplicationOutcome.PENDING.value,
        "outcome_at": pkg.outcome_at.isoformat() if pkg.outcome_at else None,
        "outcome_notes": pkg.outcome_notes,
        "ats_score": pkg.ats_score,
        "ats_breakdown": json.loads(pkg.ats_breakdown) if pkg.ats_breakdown else None,
        "credibility_score": pkg.credibility_score,
        "credibility": json.loads(pkg.credibility_findings) if pkg.credibility_findings else None,
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

    # Delete means delete. Without this the job's seen-marker outlives it and
    # every later fetch skips the listing as `already_seen`, so a job removed
    # once could never come back.
    markers = await forget_job_markers(db, jobs)
    log.info(
        "purged %d jobs, cleared %d seen-markers and %d rejected rows",
        len(jobs), markers["seen_markers"], markers["rejected_rows"],
    )
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
        # Without this, a rejected job that still carries a resume package
        # would be re-derived as jd_ready and look ready to work on again.
        JobStatus.REJECTED.value,
        JobStatus.SCAN_FAILED.value,
        JobStatus.NEEDS_MANUAL.value,
        # An interview outranks anything the packages can say. Deriving from
        # them would put the listing back to jd_ready the moment a resume is
        # regenerated, and the stage a person set by hand would be gone.
        JobStatus.INTERVIEW.value,
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


def _load_profile(profile: str | None):
    """Resolve a persona once per request.

    Doing this inside _job_dict meant 50 rows re-read personas.yml and a 71KB
    prompt file 50 times, which was most of a one-second page load.
    """
    if not profile:
        return None
    try:
        return load_persona(profile)
    except ValueError:
        return None


def _feasibility_for(job: Job, persona: Any) -> dict[str, Any] | None:
    """How well the viewing profile matches this posting.

    Computed rather than stored: the answer depends on which persona is
    looking, and writing on a GET would have every page load fight the
    background workers for the SQLite lock.
    """
    if persona is None or not job.description:
        return None
    breakdown = technical_feasibility(persona, job)
    return {
        "score": breakdown.score,
        "matched": breakdown.matched[:12],
        "missing": breakdown.missing[:12],
        "notes": breakdown.notes,
    }


def _job_dict(
    job: Job, *, profile: str | None = None, persona: Any = _UNSET
) -> dict[str, Any]:
    if persona is _UNSET:
        persona = _load_profile(profile)
    packages = [_package_dict(p) for p in getattr(job, "packages", None) or []]
    packages.sort(key=lambda p: p.get("persona_id") or "")
    my_package = next((p for p in packages if p["persona_id"] == profile), None)
    domains = getattr(job, "domains", None)
    if not domains and job.title:
        from app.services.job_domains import classify_domains, domains_storage

        domains = domains_storage(classify_domains(job.title, job.description))
    return {
        "id": job.id,
        "seq_no": job.seq_no,
        "company": job.company,
        "title": job.title,
        "url": job.url,
        "apply_url": job.apply_url,
        "location": job.location,
        "country": job.country or "US",
        "country_name": country_name(job.country),
        "source": job.source,
        "external_id": job.external_id,
        "description": job.description,
        "status": _listing_pipeline_status(job),
        "folder_path": job.folder_path,
        "persona_id": job.persona_id,
        "staffing_firm": job.staffing_firm,
        "domains": domains,
        "jd_fetched": job.jd_fetched,
        "feasibility": _feasibility_for(job, persona),
        "ats_score": (my_package or {}).get("ats_score"),
        "credibility_score": (my_package or {}).get("credibility_score"),
        "notes": (my_package or {}).get("notes") or job.notes,
        "packages": packages,
        "package_count": len(packages),
        "my_status": my_package["status"] if my_package else "need_resume",
        "my_package_id": my_package["id"] if my_package else None,
        "applied_count": sum(1 for p in packages if p["status"] == PackageStatus.APPLIED.value),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.get("/countries")
async def job_countries(db: AsyncSession = Depends(get_db)):
    """Countries that actually have listings, with a count each.

    Only what is present is offered, so the filter can never select an empty
    set. Ordered by count so the busiest country sits at the top.
    """
    rows = (
        await db.execute(
            select(Job.country, func.count(Job.id))
            .group_by(Job.country)
            .order_by(func.count(Job.id).desc())
        )
    ).all()
    total = 0
    countries = []
    for code, count in rows:
        code = (code or "US").upper()
        total += int(count)
        countries.append({"code": code, "name": country_name(code), "count": int(count)})
    return {"countries": countries, "total": total}


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
    country: str | None = Query(
        None, description="Two-letter country code; empty shows every country"
    ),
    hide_own_employers: bool = Query(
        True, description="Hide jobs at companies this profile claims to work for"
    ),
    sort: str | None = Query(None, description="seq|domains|company|title|status"),
    sort_dir: str | None = Query("asc", description="asc|desc"),
    db: AsyncSession = Depends(get_db),
):
    """List jobs with pagination. No hard cap on total rows — page through all matches."""
    from sqlalchemy import and_, or_
    from app.services.persona import list_personas

    filters = []
    if persona_id:
        filters.append(Job.persona_id == persona_id)

    # Empty means every country, which is the default the operator asked for.
    # Rows written before the column existed carry the 'US' the migration gave
    # them, so no NULL handling is needed here.
    wanted_country = (country or "").strip().upper()
    if wanted_country:
        filters.append(Job.country == wanted_country)

    # A resume claiming to work at UnitedHealth cannot be sent to UnitedHealth,
    # and an Optum posting is the same employer. Hidden per profile, never
    # deleted — another persona can still apply to it.
    own_employers: set[str] = set()
    own_filter = None
    page_persona = _load_profile(profile)
    if hide_own_employers and page_persona is not None:
        own_employers = persona_employers(page_persona)
        if own_employers:
            own_filter = Job.normalized_company.notin_(own_employers)
            filters.append(own_filter)
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
                # Where the listing lives and where the application is made.
                # Searching "dice" or "greenhouse" finds jobs by their host,
                # which no other searchable field carries.
                Job.url.ilike(like),
                Job.apply_url.ilike(like),
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

    sort_key = (sort or "seq").strip().lower()
    descending = (sort_dir or "asc").strip().lower() == "desc"
    order_cols = {
        "seq": Job.seq_no,
        "domains": Job.domains,
        "country": Job.country,
        "company": Job.company,
        "title": Job.title,
        "status": Job.status,
    }
    primary = order_cols.get(sort_key, Job.seq_no)
    primary_order = primary.desc() if descending else primary.asc()
    # Keep null/empty domains at the end when sorting by Dom
    if sort_key == "domains":
        from sqlalchemy import case

        nulls_last = case((Job.domains.is_(None), 1), (Job.domains == "", 1), else_=0)
        order_by = (nulls_last.asc(), primary_order, Job.id.desc())
    else:
        order_by = (primary_order, Job.id.desc())

    # How many the employer filter removed, so the operator sees a number
    # rather than silently missing rows.
    hidden_own = 0
    if own_filter is not None:
        others = [f for f in filters if f is not own_filter]
        without = select(func.count(Job.id))
        if others:
            without = without.where(*others)
        total_without = int((await db.execute(without)).scalar() or 0)
        hidden_own = max(0, total_without - total)

    stmt = select(Job).options(selectinload(Job.packages)).order_by(*order_by)
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.offset(start).limit(size)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "jobs": [_job_dict(j, profile=profile, persona=page_persona) for j in rows],
        "total": total,
        "page": current_page,
        "page_size": size,
        "pages": pages,
        "offset": start,
        "has_more": start + len(rows) < total,
        "sort": sort_key,
        "hidden_own_employers": hidden_own,
        "own_employer_names": sorted(own_employers)[:40],
        "sort_dir": "desc" if descending else "asc",
    }


@router.get("/meta/stats")
async def job_stats(db: AsyncSession = Depends(get_db)):
    from app.services.persona import list_personas

    rows = (
        await db.execute(select(Job.status, func.count()).group_by(Job.status))
    ).all()
    by_status = {s: c for s, c in rows}
    total = sum(by_status.values())
    max_seq = int((await db.execute(select(func.max(Job.seq_no)))).scalar() or 0)
    min_seq = int((await db.execute(select(func.min(Job.seq_no)))).scalar() or 0)
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
    from app.services.ats_catalog import catalog_stats

    # Do not seed here — every UI poll would write and fight bg workers on SQLite.
    ats = await catalog_stats(db)

    # Applications sent since the counter was last reset. That reset is
    # manual: an automatic midnight rollover wiped the number mid-session,
    # and a run that spans midnight is still one sitting's work. Until it has
    # ever been reset this falls back to the local day — a UTC boundary would
    # have rolled over mid-afternoon in US timezones.
    start_local = runtime_settings.applied_counter_since() or (
        datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    )
    today_rows = (
        await db.execute(
            select(JobPackage.persona_id, func.count())
            .where(
                JobPackage.status == PackageStatus.APPLIED.value,
                JobPackage.applied_at.is_not(None),
                JobPackage.applied_at >= start_local.astimezone(timezone.utc),
            )
            .group_by(JobPackage.persona_id)
        )
    ).all()
    applied_today = {pid: count for pid, count in today_rows}

    return {
        "total": total,
        "applied_today": sum(applied_today.values()),
        "applied_today_by_profile": applied_today,
        "today_started_at": start_local.isoformat(),
        "today_reset_manually": runtime_settings.applied_counter_since() is not None,
        "max_seq": max_seq,
        "min_seq": min_seq or (1 if total else 0),
        "by_status": by_status,
        "by_profile": by_profile,
        "ats": ats,
    }


@router.post("")
async def create_job(body: JobCreate, db: AsyncSession = Depends(get_db)):
    result = await process_listing(
        db,
        body.model_dump(exclude_none=True),
        persona_id=body.persona_id or get_active_persona_id(),
        skip_content_gates=True,
    )
    await db.commit()
    if result.reason in {"company_already_applied", "company_blocked"}:
        raise HTTPException(409, detail=result.reason)
    if result.reason == "duplicate_url" and result.job:
        job = await _get_job_with_packages(db, result.job.id)
        return _job_dict(job or result.job)
    if not result.ok:
        raise HTTPException(400, detail=result.reason or "insert_failed")
    job = await _get_job_with_packages(db, result.job.id)
    return _job_dict(job or result.job)


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


@router.post("/meta/stats/reset-today")
async def reset_today_counter():
    """Start the Today counter from now.

    Nothing is deleted. The count is derived from JobPackage.applied_at, so
    clearing it would mean erasing real applied history; this moves the mark
    the count runs from instead, and every application stays on record.
    """
    marker = runtime_settings.reset_applied_counter()
    log.info("today counter reset to %s", marker.isoformat())
    return {"ok": True, "today_started_at": marker.isoformat(), "applied_today": 0}


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
async def get_job(
    job_id: int,
    profile: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job_with_packages(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    # Without the profile there is no "my package" and no fit score — the
    # single-job fetch has to answer for the same persona the list did.
    return _job_dict(job, profile=profile or get_active_persona_id())


@router.get("/{job_id}/qa")
async def get_job_qa(
    job_id: int,
    persona_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job_with_packages(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    pkg = next((p for p in (job.packages or []) if p.persona_id == persona_id), None)
    folder = Path(pkg.folder_path) if pkg and pkg.folder_path else None
    if not folder or not folder.exists():
        return {"ok": True, "questions": [], "answers": [], "folder": None}

    questions: list[str] = []
    answers: list[dict[str, str]] = []
    q_path = folder / "questions.json"
    a_path = folder / "answers.json"
    try:
        if q_path.exists():
            raw_q = json.loads(q_path.read_text(encoding="utf-8"))
            if isinstance(raw_q, list):
                questions = [str(q) for q in raw_q]
    except (OSError, json.JSONDecodeError):
        questions = []
    try:
        if a_path.exists():
            raw_a = json.loads(a_path.read_text(encoding="utf-8"))
            if isinstance(raw_a, dict):
                answers = [{"question": k, "answer": str(v or "")} for k, v in raw_a.items()]
            elif isinstance(raw_a, list):
                answers = [
                    {"question": str(row.get("question") or ""), "answer": str(row.get("answer") or "")}
                    for row in raw_a
                    if isinstance(row, dict)
                ]
    except (OSError, json.JSONDecodeError):
        answers = []
    if answers and not questions:
        questions = [row["question"] for row in answers if row.get("question")]
    return {
        "ok": True,
        "folder": str(folder),
        "questions": questions,
        "answers": answers,
    }


def _package_folder(job: Job, persona_id: str | None) -> tuple[JobPackage | None, Path | None]:
    pkgs = list(job.packages or [])
    pkg = None
    if persona_id:
        pkg = next((p for p in pkgs if p.persona_id == persona_id), None)
    if pkg is None:
        pkg = next((p for p in pkgs if p.folder_path), None)
    folder = Path(pkg.folder_path) if pkg and pkg.folder_path else None
    if folder is None and job.folder_path:
        folder = Path(job.folder_path)
    return pkg, folder


@router.get("/{job_id}/artifacts")
async def list_job_artifacts(
    job_id: int,
    persona_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List generated resume/cover files for in-app preview (no folder browsing)."""
    job = await _get_job_with_packages(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    pkg, folder = _package_folder(job, persona_id)
    if folder is not None and not folder_is_allowed(folder):
        folder = None
    artifacts = resolve_artifacts(folder)
    files = {
        kind: {
            "name": art.name,
            "size": art.size,
            "mtime": int(art.mtime),
        }
        for kind, art in artifacts.items()
    }
    return {
        "ok": True,
        "job_id": job.id,
        "persona_id": pkg.persona_id if pkg else persona_id,
        "folder": str(folder) if folder else None,
        "available": sorted(files.keys()),
        "files": files,
    }


@router.get("/{job_id}/artifacts/{kind}")
async def get_job_artifact(
    job_id: int,
    kind: str,
    persona_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Serve a generated resume/cover file inline so the UI can preview it."""
    if kind not in KINDS:
        raise HTTPException(404, detail="unknown_artifact")
    job = await _get_job_with_packages(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    _pkg, folder = _package_folder(job, persona_id)
    try:
        art = resolve_kind(folder, kind)
    except KeyError:
        raise HTTPException(404, detail="unknown_artifact") from None
    except FileNotFoundError:
        raise HTTPException(404, detail="artifact_missing") from None
    return FileResponse(
        path=art.path,
        media_type=art.media_type,
        filename=art.name,
        content_disposition_type="inline",
    )


def _rename_job_folders(job: Job, old_company: str, new_company: str) -> None:
    if not job.seq_no or old_company == new_company:
        return
    old_name = job_folder_name(job.seq_no, old_company)
    new_name = job_folder_name(job.seq_no, new_company)
    if old_name == new_name:
        return
    root = get_settings().output_root
    if not root.exists():
        return
    for persona_dir in root.iterdir():
        if not persona_dir.is_dir():
            continue
        old_path = persona_dir / old_name
        new_path = persona_dir / new_name
        if old_path.exists() and old_path.is_dir() and not new_path.exists():
            old_path.rename(new_path)
    if job.folder_path:
        job.folder_path = job.folder_path.replace(old_name, new_name)
    for pkg in job.packages or []:
        if pkg.folder_path:
            pkg.folder_path = pkg.folder_path.replace(old_name, new_name)


@router.patch("/{job_id}")
async def patch_job_fields(
    job_id: int, body: JobFieldsPatch, db: AsyncSession = Depends(get_db)
):
    job = await _get_job_with_packages(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")

    old_company = job.company
    if body.company is not None:
        company = body.company.strip()
        if not company:
            raise HTTPException(400, detail="company_required")
        job.company = company
        job.normalized_company = normalize_company(company)
        company_row = await _get_or_create_company(db, company, job.normalized_company)
        job.company_id = company_row.id
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(400, detail="title_required")
        job.title = title
    if body.url is not None:
        url = body.url.strip()
        if not url:
            raise HTTPException(400, detail="url_required")
        old_url = job.url
        job.url = url
        if not job.apply_url or job.apply_url == old_url:
            job.apply_url = url
    if body.apply_url is not None:
        apply_url = body.apply_url.strip()
        job.apply_url = apply_url or job.url
    if body.location is not None:
        job.location = body.location.strip() or None

    job.updated_at = utcnow()
    if job.company != old_company:
        _rename_job_folders(job, old_company, job.company)

    if job.seq_no:
        personas = {job.persona_id}
        for pkg in job.packages or []:
            if pkg.persona_id:
                personas.add(pkg.persona_id)
        for pid in personas:
            sync_job_folder(
                persona_id=pid,
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
    # Both take the job out of the pipeline, so an apply already under way on
    # it has to stop with it — otherwise a rejected job could still submit.
    if target in {JobStatus.SKIPPED.value, JobStatus.REJECTED.value}:
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


@router.patch("/{job_id}/outcome")
async def patch_outcome(
    job_id: int, body: OutcomePatch, db: AsyncSession = Depends(get_db)
):
    """Record what the employer did after we applied."""
    try:
        outcome = ApplicationOutcome(body.outcome)
    except ValueError as exc:
        raise HTTPException(400, detail=f"invalid_outcome:{body.outcome}") from exc

    job = await _get_job_with_packages(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")

    pkgs = list(job.packages or [])
    if body.package_id is not None:
        pkg = next((p for p in pkgs if p.id == body.package_id), None)
    elif body.persona_id:
        pkg = next((p for p in pkgs if p.persona_id == body.persona_id), None)
    elif len(pkgs) == 1:
        pkg = pkgs[0]
    else:
        raise HTTPException(400, detail="multiple_packages_pick_profile")
    if not pkg:
        raise HTTPException(404, detail="package_not_found")
    if pkg.status != PackageStatus.APPLIED.value:
        raise HTTPException(400, detail=f"not_applied:{pkg.status}")

    pkg.outcome = outcome.value
    pkg.outcome_at = utcnow()
    pkg.outcome_notes = (body.notes or "").strip() or None
    await db.commit()
    return _package_dict(pkg)


@router.get("/meta/outcomes")
async def outcome_stats(
    persona_id: str | None = Query(default=None), db: AsyncSession = Depends(get_db)
):
    """Response funnel per persona. Applying is not the outcome."""
    where = [JobPackage.status == PackageStatus.APPLIED.value]
    if persona_id:
        where.append(JobPackage.persona_id == persona_id)

    rows = (
        await db.execute(
            select(JobPackage.persona_id, JobPackage.outcome, func.count())
            .where(*where)
            .group_by(JobPackage.persona_id, JobPackage.outcome)
        )
    ).all()

    by_profile: dict[str, dict[str, int]] = {}
    for pid, outcome, count in rows:
        by_profile.setdefault(pid, {})[outcome or ApplicationOutcome.PENDING.value] = count

    summary: dict[str, Any] = {}
    for pid, counts in by_profile.items():
        applied = sum(counts.values())
        responses = sum(counts.get(o, 0) for o in RESPONSE_OUTCOMES)
        # Pending applications have not had their chance yet, so the rate is
        # measured against those that actually resolved one way or the other.
        resolved = applied - counts.get(ApplicationOutcome.PENDING.value, 0)
        summary[pid] = {
            "applied": applied,
            "resolved": resolved,
            "responses": responses,
            "interviews": counts.get(ApplicationOutcome.INTERVIEW.value, 0)
            + counts.get(ApplicationOutcome.OFFER.value, 0),
            "response_rate": round(responses / resolved, 4) if resolved else None,
            "by_outcome": counts,
        }
    return {
        "outcomes": [o.value for o in ApplicationOutcome],
        "by_profile": summary,
    }


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
