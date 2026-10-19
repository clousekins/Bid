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
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.enums import (
    RESPONSE_OUTCOMES,
    ApplicationOutcome,
    JobStatus,
    can_transition,
)
from app.db.job_tables import job_model, resolve_job
from app.db.models import Application, JobIndex, JobRow, utcnow
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
from app.services.persona import all_persona_ids, get_active_persona_id, load_persona
from app.services.pipeline import (
    _get_or_create_company,
    applied_scope,
    effective_disabled,
    forget_job_markers,
    is_disabled_for,
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
    salary: str | None = None
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


class DisableBody(BaseModel):
    persona_id: str | None = None


class JobFieldsPatch(BaseModel):
    company: str | None = None
    title: str | None = None
    url: str | None = None
    location: str | None = None
    apply_url: str | None = None
    salary: str | None = None


class RangeDelete(BaseModel):
    seq_from: int = Field(..., ge=1)
    seq_to: int = Field(..., ge=1)
    remove_folders: bool = True


class IdsDelete(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=2000)
    remove_folders: bool = True


def _row_pkg_dict(job: JobRow) -> dict[str, Any]:
    """The row's own resume/apply state, in the shape the UI expects of a package."""
    return {
        "id": job.id,
        "persona_id": job.persona_id,
        "status": job.status,
        "folder_path": job.folder_path,
        "notes": job.notes,
        "applied_at": job.applied_at.isoformat() if job.applied_at else None,
        "outcome": job.outcome or ApplicationOutcome.PENDING.value,
        "outcome_at": job.outcome_at.isoformat() if job.outcome_at else None,
        "outcome_notes": job.outcome_notes,
        "ats_score": job.ats_score,
        "ats_breakdown": json.loads(job.ats_breakdown) if job.ats_breakdown else None,
        "credibility_score": job.credibility_score,
        "credibility": json.loads(job.credibility_findings) if job.credibility_findings else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


async def _get_job(db: AsyncSession, job_id: int) -> JobRow | None:
    """One row by its global id, routed to the right persona table."""
    resolved = await resolve_job(db, job_id)
    return resolved[1] if resolved else None


async def _all_job_rows(
    db: AsyncSession, *, seq_from: int | None = None, seq_to: int | None = None
) -> list[JobRow]:
    """Every job row across every persona table, optionally within a seq range.

    seq_no is per-persona now, so a range spans each persona's own numbering.
    """
    out: list[JobRow] = []
    for pid in all_persona_ids():
        model = job_model(pid)
        conds = []
        if seq_from is not None:
            conds.append(model.seq_no >= seq_from)
        if seq_to is not None:
            conds.append(model.seq_no <= seq_to)
        stmt = select(model)
        if conds:
            stmt = stmt.where(*conds)
        out.extend((await db.execute(stmt.order_by(model.seq_no.asc()))).scalars().all())
    return out


async def _rows_by_ids(db: AsyncSession, ids: list[int]) -> list[JobRow]:
    rows = []
    for jid in ids:
        row = await _get_job(db, jid)
        if row is not None:
            rows.append(row)
    return rows


async def _purge_jobs(
    db: AsyncSession,
    jobs: list[JobRow],
    *,
    remove_folders: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    deleted: list[dict[str, Any]] = []
    folders_removed = 0
    for job in jobs:
        deleted.append(
            {
                "id": job.id,
                "seq_no": job.seq_no,
                "company": job.company,
                "title": job.title,
                "folder_path": job.folder_path,
                "packages": [_row_pkg_dict(job)],
            }
        )
        if remove_folders:
            if remove_all_persona_outputs(
                seq_no=job.seq_no,
                company=job.company,
                folder_paths=[job.folder_path],
                persona_ids=[job.persona_id],
            ):
                folders_removed += 1
        await db.execute(
            update(Application).where(Application.job_id == job.id).values(job_id=None)
        )
        await db.execute(delete(JobIndex).where(JobIndex.id == job.id))
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


# A row "needs a resume" when its own status is still in the JD lifecycle — no
# resume has been generated (or the last run failed and can be retried).
_NEEDS_RESUME_STATUS = {
    JobStatus.NEW.value,
    JobStatus.JD_READY.value,
    JobStatus.SCAN_FAILED.value,
    JobStatus.TAILOR_FAILED.value,
}


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


def _feasibility_for(job: JobRow, persona: Any) -> dict[str, Any] | None:
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
    job: JobRow,
    *,
    profile: str | None = None,
    persona: Any = _UNSET,
    disabled: bool = False,
    disabled_reason: str | None = None,
) -> dict[str, Any]:
    if persona is _UNSET:
        persona = _load_profile(profile or job.persona_id)
    # The row is the persona's own copy, so it is its own single "package".
    row_pkg = _row_pkg_dict(job)
    domains = getattr(job, "domains", None)
    if not domains and job.title:
        from app.services.job_domains import classify_domains, domains_storage

        domains = domains_storage(classify_domains(job.title, job.description))
    needs_resume = job.status in _NEEDS_RESUME_STATUS
    return {
        "id": job.id,
        "seq_no": job.seq_no,
        "company": job.company,
        "title": job.title,
        "url": job.url,
        "apply_url": job.apply_url,
        "location": job.location,
        "salary": job.salary,
        "country": job.country or "US",
        "country_name": country_name(job.country),
        "source": job.source,
        "external_id": job.external_id,
        "description": job.description,
        "status": job.status,
        "folder_path": job.folder_path,
        "persona_id": job.persona_id,
        "staffing_firm": job.staffing_firm,
        "domains": domains,
        "jd_fetched": job.jd_fetched,
        # Disabled for this persona: an applied-company sibling
        # ("company_applied") or one turned off by hand ("manual"). The UI
        # greys it and keeps ingestion / resume build off it.
        "disabled": disabled,
        "disabled_reason": disabled_reason,
        "feasibility": _feasibility_for(job, persona),
        "ats_score": job.ats_score,
        "credibility_score": job.credibility_score,
        "notes": job.notes,
        "packages": [row_pkg],
        "package_count": 1,
        "my_status": "need_resume" if needs_resume else job.status,
        "my_package_id": job.id,
        "applied_count": 1 if job.status == JobStatus.APPLIED.value else 0,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.get("/countries")
async def job_countries(db: AsyncSession = Depends(get_db)):
    """Countries that actually have listings, with a count each.

    Only what is present is offered, so the filter can never select an empty
    set. Ordered by count so the busiest country sits at the top.
    """
    counts: dict[str, int] = {}
    for pid in all_persona_ids():
        model = job_model(pid)
        rows = (
            await db.execute(
                select(model.country, func.count(model.id)).group_by(model.country)
            )
        ).all()
        for code, count in rows:
            code = (code or "US").upper()
            counts[code] = counts.get(code, 0) + int(count)
    total = sum(counts.values())
    countries = [
        {"code": code, "name": country_name(code), "count": n}
        for code, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
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
    """List one persona's own jobs, paginated. The table is the persona, so a
    ``profile`` (or ``persona_id``) selects which list; it falls back to the
    active persona."""
    from sqlalchemy import or_

    pid = profile or persona_id or get_active_persona_id()
    model = job_model(pid)
    filters = []

    # Empty means every country, which is the default the operator asked for.
    wanted_country = (country or "").strip().upper()
    if wanted_country:
        filters.append(model.country == wanted_country)

    # A resume claiming to work at UnitedHealth cannot be sent to UnitedHealth,
    # and an Optum posting is the same employer. Hidden per persona, never
    # deleted — another persona can still apply to it.
    own_employers: set[str] = set()
    own_filter = None
    page_persona = _load_profile(pid)
    if hide_own_employers and page_persona is not None:
        own_employers = persona_employers(page_persona)
        if own_employers:
            own_filter = model.normalized_company.notin_(own_employers)
            filters.append(own_filter)

    # A per-persona status filter on the row's own status.
    if profile_status:
        if profile_status == "need_resume":
            filters.append(model.status.in_(list(_NEEDS_RESUME_STATUS)))
        else:
            filters.append(model.status == profile_status)

    # Applied-company duplicates are no longer hidden. They stay in the list and
    # are marked disabled at serialization time (below), so the UI can grey them.
    if status:
        filters.append(model.status == status)
    if seq_from is not None:
        filters.append(model.seq_no >= seq_from)
    if seq_to is not None:
        filters.append(model.seq_no <= seq_to)
    if q:
        term = q.strip()
        if term:
            like = f"%{term}%"
            filters.append(
                or_(
                    model.company.ilike(like),
                    model.title.ilike(like),
                    # Searching "dice" or "greenhouse" finds jobs by their host.
                    model.url.ilike(like),
                    model.apply_url.ilike(like),
                )
            )

    count_stmt = select(func.count(model.id))
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
        "seq": model.seq_no,
        "domains": model.domains,
        "country": model.country,
        "company": model.company,
        "title": model.title,
        "status": model.status,
    }
    primary = order_cols.get(sort_key, model.seq_no)
    primary_order = primary.desc() if descending else primary.asc()
    # Keep null/empty domains at the end when sorting by Dom
    if sort_key == "domains":
        from sqlalchemy import case

        nulls_last = case((model.domains.is_(None), 1), (model.domains == "", 1), else_=0)
        order_by = (nulls_last.asc(), primary_order, model.id.desc())
    else:
        order_by = (primary_order, model.id.desc())

    # How many the employer filter removed, so the operator sees a number.
    hidden_own = 0
    if own_filter is not None:
        others = [f for f in filters if f is not own_filter]
        without = select(func.count(model.id))
        if others:
            without = without.where(*others)
        total_without = int((await db.execute(without)).scalar() or 0)
        hidden_own = max(0, total_without - total)

    stmt = select(model).order_by(*order_by)
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.offset(start).limit(size)
    rows = (await db.execute(stmt)).scalars().all()

    # Mark each row disabled — applied-company siblings and hand-disabled rows
    # alike — from a couple of indexed passes rather than a subquery per row.
    applied_company_set, applied_job_ids = await applied_scope(db, pid)
    job_dicts: list[dict[str, Any]] = []
    disabled_count = 0
    for j in rows:
        dis, reason = effective_disabled(j, applied_company_set, applied_job_ids)
        if dis:
            disabled_count += 1
        job_dicts.append(
            _job_dict(
                j,
                profile=pid,
                persona=page_persona,
                disabled=dis,
                disabled_reason=reason,
            )
        )
    return {
        "jobs": job_dicts,
        "total": total,
        "page": current_page,
        "page_size": size,
        "pages": pages,
        "offset": start,
        "has_more": start + len(rows) < total,
        "sort": sort_key,
        "hidden_own_employers": hidden_own,
        "own_employer_names": sorted(own_employers)[:40],
        "disabled_count": disabled_count,
        "sort_dir": "desc" if descending else "asc",
    }


@router.get("/meta/stats")
async def job_stats(db: AsyncSession = Depends(get_db)):
    from app.services.persona import list_personas

    start_local = runtime_settings.applied_counter_since() or (
        datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    )
    cutoff = start_local.astimezone(timezone.utc)

    by_status: dict[str, int] = {}
    by_profile: dict[str, dict[str, int]] = {}
    applied_today: dict[str, int] = {}
    max_seq = 0
    min_seq = 0
    pids = all_persona_ids()
    for pid in pids:
        model = job_model(pid)
        rows = (
            await db.execute(select(model.status, func.count()).group_by(model.status))
        ).all()
        counts = {s: int(c) for s, c in rows}
        if counts:
            by_profile[pid] = dict(counts)
        for s, c in counts.items():
            by_status[s] = by_status.get(s, 0) + c
        mx = int((await db.execute(select(func.max(model.seq_no)))).scalar() or 0)
        mn = int((await db.execute(select(func.min(model.seq_no)))).scalar() or 0)
        max_seq = max(max_seq, mx)
        if mn and (min_seq == 0 or mn < min_seq):
            min_seq = mn
        n_today = int(
            (
                await db.execute(
                    select(func.count()).where(
                        model.status == JobStatus.APPLIED.value,
                        model.applied_at.is_not(None),
                        model.applied_at >= cutoff,
                    )
                )
            ).scalar()
            or 0
        )
        if n_today:
            applied_today[pid] = n_today
    total = sum(by_status.values())
    for persona in list_personas():
        pid = persona["id"]
        statuses = by_profile.setdefault(pid, {})
        statuses["need_resume"] = sum(statuses.get(s, 0) for s in _NEEDS_RESUME_STATUS)
    from app.services.ats_catalog import catalog_stats

    # Do not seed here — every UI poll would write and fight bg workers on SQLite.
    ats = await catalog_stats(db)

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
    # A URL repeat or another recent listing from the same company: hand back
    # the row already on file instead of a raw error.
    if result.reason in {"duplicate_url", "duplicate_company"} and result.job:
        return _job_dict(result.job)
    if not result.ok:
        raise HTTPException(400, detail=result.reason or "insert_failed")
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
    rows = await _all_job_rows(db)
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

    Nothing is deleted. The count is derived from each row's applied_at, so
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
    rows = await _all_job_rows(db, seq_from=body.seq_from, seq_to=body.seq_to)
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
    rows = await _rows_by_ids(db, ids)
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
    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    # The row belongs to one persona's table — answer for that persona.
    prof = job.persona_id
    dis, reason = await is_disabled_for(db, job, prof)
    return _job_dict(job, profile=prof, disabled=dis, disabled_reason=reason)


@router.get("/{job_id}/qa")
async def get_job_qa(
    job_id: int,
    persona_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    folder = Path(job.folder_path) if job.folder_path else None
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


def _row_folder(job: JobRow) -> Path | None:
    return Path(job.folder_path) if job.folder_path else None


@router.get("/{job_id}/artifacts")
async def list_job_artifacts(
    job_id: int,
    persona_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List generated resume/cover files for in-app preview (no folder browsing)."""
    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    folder = _row_folder(job)
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
        "persona_id": job.persona_id,
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
    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    folder = _row_folder(job)
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


def _rename_job_folders(job: JobRow, old_company: str, new_company: str) -> None:
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


@router.patch("/{job_id}")
async def patch_job_fields(
    job_id: int, body: JobFieldsPatch, db: AsyncSession = Depends(get_db)
):
    job = await _get_job(db, job_id)
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
    if body.salary is not None:
        job.salary = body.salary.strip() or None

    job.updated_at = utcnow()
    if job.company != old_company:
        _rename_job_folders(job, old_company, job.company)

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
    return _job_dict(job)


@router.delete("/{job_id}")
async def delete_job(
    job_id: int,
    remove_folders: bool = True,
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id)
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
    job = await _get_job(db, job_id)
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
        return _job_dict(job)
    if target in {JobStatus.RESUME_READY.value, JobStatus.APPLYING.value}:
        # These are reached by the tailor/apply flow, not set by hand.
        raise HTTPException(400, detail="status_set_by_tailor_or_apply")

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
    return _job_dict(job)


@router.patch("/{job_id}/outcome")
async def patch_outcome(
    job_id: int, body: OutcomePatch, db: AsyncSession = Depends(get_db)
):
    """Record what the employer did after we applied."""
    try:
        outcome = ApplicationOutcome(body.outcome)
    except ValueError as exc:
        raise HTTPException(400, detail=f"invalid_outcome:{body.outcome}") from exc

    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    if body.persona_id and body.persona_id != job.persona_id:
        raise HTTPException(404, detail="package_not_found")
    if job.status != JobStatus.APPLIED.value:
        raise HTTPException(400, detail=f"not_applied:{job.status}")

    job.outcome = outcome.value
    job.outcome_at = utcnow()
    job.outcome_notes = (body.notes or "").strip() or None
    await db.commit()
    return _row_pkg_dict(job)


@router.get("/meta/outcomes")
async def outcome_stats(
    persona_id: str | None = Query(default=None), db: AsyncSession = Depends(get_db)
):
    """Response funnel per persona. Applying is not the outcome."""
    pids = [persona_id] if persona_id else all_persona_ids()
    by_profile: dict[str, dict[str, int]] = {}
    for pid in pids:
        model = job_model(pid)
        rows = (
            await db.execute(
                select(model.outcome, func.count())
                .where(model.status == JobStatus.APPLIED.value)
                .group_by(model.outcome)
            )
        ).all()
        counts = {
            (outcome or ApplicationOutcome.PENDING.value): int(count)
            for outcome, count in rows
        }
        if counts:
            by_profile[pid] = counts

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


async def _set_job_disabled(
    db: AsyncSession, job_id: int, persona_id: str | None, disabled: bool
) -> dict[str, Any]:
    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    # enabled is the folded tri-state: disable -> False (off by hand),
    # enable -> True (forced on, overriding the auto company-applied rule).
    job.enabled = not disabled
    job.updated_at = utcnow()
    await db.commit()
    pid = job.persona_id
    dis, reason = await is_disabled_for(db, job, pid)
    return _job_dict(job, profile=pid, disabled=dis, disabled_reason=reason)


@router.post("/{job_id}/disable")
async def disable_job(
    job_id: int, body: DisableBody | None = None, db: AsyncSession = Depends(get_db)
):
    """Turn this job off for one profile: greyed in the list, and left alone by
    ingestion and resume build."""
    return await _set_job_disabled(db, job_id, body.persona_id if body else None, True)


@router.post("/{job_id}/enable")
async def enable_job(
    job_id: int, body: DisableBody | None = None, db: AsyncSession = Depends(get_db)
):
    """Turn this job back on for one profile, overriding an automatic
    applied-company disable."""
    return await _set_job_disabled(db, job_id, body.persona_id if body else None, False)


@router.post("/{job_id}/block-company")
async def block_company(job_id: int, db: AsyncSession = Depends(get_db)):
    from app.db.models import Company

    job = await _get_job(db, job_id)
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
    job = await _get_job(db, job_id)
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
    job = await _get_job(db, job_id)
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
    job = await _get_job(db, job_id)
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

    job = await _get_job(db, job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    qs = []
    for m in re.finditer(r"(?m)^\s*(?:\d+[.)]\s*)?([A-Z][^?\n]{8,120}\?)\s*$", job.description or ""):
        qs.append(m.group(1).strip())
    return {"questions": qs}
