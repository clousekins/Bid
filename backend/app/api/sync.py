from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.enums import BackgroundJobKind, JobStatus, PackageStatus
from app.db.models import Job, JobPackage
from app.db.session import get_db
from app.services import bg_jobs
from app.services.ats_catalog import catalog_stats, list_boards, seed_ats_catalog
from app.services.fetch_sites import (
    list_fetch_sites,
    resolve_site,
    resolve_window,
    window_cap,
)
from app.services.job_domains import list_job_domains, normalize_domain_ids
from app.services.persona import get_active_persona_id

router = APIRouter(prefix="/api/sync", tags=["sync"])


class RangeBody(BaseModel):
    persona_id: str | None = None
    seq_from: int | None = 1
    seq_to: int | None = 50
    limit: int = 50
    job_ids: list[int] | None = None
    domains: list[str] | None = None  # e.g. ["de","ai","sf"] — filter discover/fresh
    # Regenerate even when this persona already has a resume package for the job.
    force: bool = False


class DiscoverSiteBody(BaseModel):
    persona_id: str | None = None
    site: str
    window: str = "7d"  # 24h | 7d | all
    # None means "as many as this window allows" — a day of listings is
    # bounded by its own date filter, so 24h does not need a second limit.
    max_jobs: int | None = None
    roles: list[str] | None = None
    domains: list[str] | None = None  # e.g. ["de","ai","sf"]


@router.get("/ats-catalog")
async def ats_catalog(db: AsyncSession = Depends(get_db)):
    # Seed only when explicitly opening catalog endpoints, not on every UI poll.
    added = await seed_ats_catalog(db)
    stats = await catalog_stats(db)
    if added:
        await db.commit()
    return stats


@router.get("/ats-catalog/boards")
async def ats_catalog_boards(
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Full company/ATS board list (searched + waiting)."""
    return await list_boards(
        db,
        status=status,
        source=source,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/fetch-sites")
async def fetch_sites_catalog():
    """Sites + time windows + tech domains available for site-scoped Fetch."""
    from app.services.job_domains import roles_for_domain

    catalog = list_fetch_sites()
    domains = list_job_domains()
    # Each domain carries the concrete titles inside it, so the picker can
    # offer "Senior Data Engineer" rather than only "Data Engineering".
    for domain in domains:
        domain["roles"] = roles_for_domain(domain.get("id"))
    catalog["domains"] = domains
    return catalog


@router.post("/discover")
async def sync_discover(body: RangeBody):
    pid = body.persona_id or get_active_persona_id()
    domains = normalize_domain_ids(body.domains)
    row = await bg_jobs.enqueue(
        kind=BackgroundJobKind.DISCOVER,
        persona_id=pid,
        options={"domains": domains} if domains else None,
    )
    return {
        "job_id": row.id,
        "kind": row.kind,
        "status": row.status,
        "persona_id": pid,
        "domains": domains,
    }


@router.post("/discover-fresh")
async def sync_discover_fresh(body: RangeBody):
    """Deep fetch jobs posted within ~24 hours from feeds + ATS boards."""
    pid = body.persona_id or get_active_persona_id()
    domains = normalize_domain_ids(body.domains)
    row = await bg_jobs.enqueue(
        kind=BackgroundJobKind.DISCOVER_FRESH,
        persona_id=pid,
        options={"domains": domains} if domains else None,
    )
    return {
        "job_id": row.id,
        "kind": row.kind,
        "status": row.status,
        "persona_id": pid,
        "domains": domains,
    }


@router.post("/discover-site")
async def sync_discover_site(body: DiscoverSiteBody):
    """Fetch from one site/feed or ATS family, bounded by the window.

    The window sets how many jobs may come back: a day of listings is limited
    by its own date filter, so 24h takes everything it finds and only the open
    "all time" window needs a hard ceiling.
    """
    site = resolve_site(body.site)
    if not site:
        raise HTTPException(400, detail=f"unknown site: {body.site}")
    win = resolve_window(body.window)
    ceiling = window_cap(win["id"])
    max_jobs = (
        ceiling
        if body.max_jobs is None
        else max(1, min(ceiling, int(body.max_jobs)))
    )
    domains = normalize_domain_ids(body.domains)
    pid = body.persona_id or get_active_persona_id()
    row = await bg_jobs.enqueue(
        kind=BackgroundJobKind.DISCOVER_SITE,
        persona_id=pid,
        options={
            "site": site["id"],
            "window": win["id"],
            "max_jobs": max_jobs,
            "roles": body.roles or [],
            "site_label": site["label"],
            "domains": domains,
        },
    )
    return {
        "job_id": row.id,
        "kind": row.kind,
        "status": row.status,
        "persona_id": pid,
        "site": site["id"],
        "window": win["id"],
        "max_jobs": max_jobs,
        "domains": domains,
    }


@router.post("/discover-companies")
async def sync_discover_companies(body: RangeBody):
    """Google/SerpAPI search for real company ATS boards; add new ones to the catalog."""
    pid = body.persona_id or get_active_persona_id()
    row = await bg_jobs.enqueue(
        kind=BackgroundJobKind.DISCOVER_COMPANIES,
        persona_id=pid,
    )
    return {"job_id": row.id, "kind": row.kind, "status": row.status, "persona_id": pid}


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
        force=bool(body.force),
    )
    return {
        "job_id": row.id,
        "kind": row.kind,
        "status": row.status,
        "persona_id": gen_pid,
        "force": bool(body.force),
    }


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


class SectorSearchBody(BaseModel):
    sector: str = Field(..., min_length=2, max_length=60)
    limit: int = Field(default=60, ge=1, le=200)
    it_only: bool = True
    us_remote_only: bool = True


@router.post("/sector-search")
async def sector_search(body: SectorSearchBody):
    """Find jobs at companies in one industry sector.

    Returns results for review rather than inserting them: a sector search
    reaches wider than the curated boards, so the operator picks what enters
    the pipeline via the normal import path.
    """
    from app.services.sector_search import search_sector

    return await search_sector(
        body.sector,
        limit=body.limit,
        it_only=body.it_only,
        us_remote_only=body.us_remote_only,
    )


@router.get("/sector-search/sectors")
async def sector_list():
    from app.services.sector_search import SECTOR_HINTS

    return {"sectors": sorted(SECTOR_HINTS)}
