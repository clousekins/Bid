from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.enums import JobStatus, PackageStatus
from app.db.models import Application, Company, Job, JobPackage, JobSourceSeen, RejectedJob
from app.services.folder_writer import sync_job_folder
from app.services.job_country import country_code
from app.services.persona import get_active_persona_id
from app.validators.dedupe import canonicalize_url, normalize_company, normalize_title
from app.validators.it_domain import check_it_domain
from app.validators.remote_us import check_remote_us
from app.validators.seniority import check_seniority

SOLID_JD = 50


@dataclass
class ProcessResult:
    ok: bool
    job: Job | None = None
    skipped: bool = False
    reason: str = ""
    status: str | None = None


async def _next_seq(session: AsyncSession) -> int:
    result = await session.execute(select(func.coalesce(func.max(Job.seq_no), 0)))
    return int(result.scalar_one()) + 1


async def _get_or_create_company(session: AsyncSession, company: str, normalized: str) -> Company:
    result = await session.execute(
        select(Company).where(Company.normalized_name == normalized)
    )
    row = result.scalar_one_or_none()
    if row:
        return row
    row = Company(name=company, normalized_name=normalized)
    session.add(row)
    await session.flush()
    return row


async def company_is_applied(session: AsyncSession, persona_id: str, normalized: str) -> bool:
    """Has this persona already applied at this company?

    Three places record it, because an apply can be logged from three: an
    Application row, the listing's own status, and a per-persona package.

    A rejected listing counts for none of them. That is the point of rejecting
    one — it says this application is written off, so the company should be
    open again for the next job that comes from there. Without the exclusions
    below, marking a job rejected would change how it looks and nothing else:
    the Application row and the applied package would still hold the door shut.
    """
    rejected = JobStatus.REJECTED.value

    # An Application either names the listing it came from or predates the
    # link. One tied to a rejected listing is written off with it; a free
    # standing one is all the record there is, so it still counts.
    result = await session.execute(
        select(Application.id)
        .outerjoin(Job, Job.id == Application.job_id)
        .where(
            Application.persona_id == persona_id,
            Application.normalized_company == normalized,
            Application.status == "applied",
            or_(Application.job_id.is_(None), Job.status != rejected),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return True

    # The listing's own status needs no exclusion: a job is applied or
    # rejected, never both.
    result = await session.execute(
        select(Job.id)
        .where(
            Job.persona_id == persona_id,
            Job.normalized_company == normalized,
            Job.status == JobStatus.APPLIED.value,
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return True

    pkg = await session.execute(
        select(JobPackage.id)
        .join(Job, Job.id == JobPackage.job_id)
        .where(
            JobPackage.persona_id == persona_id,
            JobPackage.status == PackageStatus.APPLIED.value,
            Job.normalized_company == normalized,
            Job.status != rejected,
        )
        .limit(1)
    )
    return pkg.scalar_one_or_none() is not None


async def find_job_by_url(session: AsyncSession, url: str) -> Job | None:
    canon = canonicalize_url(url)
    if not canon:
        return None
    variants = {url.strip(), url.strip().rstrip("/"), canon}
    row = (
        await session.execute(
            select(Job)
            .options(selectinload(Job.packages))
            .where(or_(Job.url.in_(variants), Job.apply_url.in_(variants)))
            .order_by(Job.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row:
        return row
    jobs = (
        await session.execute(select(Job).options(selectinload(Job.packages)).order_by(Job.id.desc()).limit(400))
    ).scalars().all()
    for job in jobs:
        if canonicalize_url(job.url or "") == canon or canonicalize_url(job.apply_url or "") == canon:
            return job
    return None



async def find_job_by_company_title(
    session: AsyncSession, normalized_company: str, title: str
) -> Job | None:
    """An existing listing for the same company and the same role."""
    key = normalize_title(title)
    if not normalized_company or not key:
        return None
    rows = (
        await session.execute(
            select(Job)
            .options(selectinload(Job.packages))
            .where(Job.normalized_company == normalized_company)
            .order_by(Job.id.desc())
        )
    ).scalars().all()
    for job in rows:
        if normalize_title(job.title or "") == key:
            return job
    return None


async def company_is_blocked(session: AsyncSession, normalized: str) -> bool:
    result = await session.execute(
        select(Company).where(Company.normalized_name == normalized, Company.blocked.is_(True))
    )
    return result.scalar_one_or_none() is not None


def _gate_status(reason: str) -> str:
    if reason in {"hybrid_or_onsite", "not_remote"}:
        return JobStatus.REJECTED_ONSITE.value
    if reason in {"junior_title", "not_it_domain", "not_us", "non_us_only"}:
        return JobStatus.REJECTED_ONSITE.value if reason != "junior_title" else JobStatus.SKIPPED.value
    return JobStatus.NEEDS_MANUAL.value


async def process_listing(
    session: AsyncSession,
    listing: dict[str, Any],
    *,
    persona_id: str | None = None,
    skip_content_gates: bool = False,
    fetch_jd: bool = False,
    insert_rejects: bool = False,
    bypass_gates: bool = False,
) -> ProcessResult:
    """Insert a job listing.

    When description is present and gates are enabled, classify as jd_ready or
    rejected_* at insert time. Deep-scan remains available for status=new /
    scan_failed rows.
    """
    del fetch_jd
    pid = persona_id or get_active_persona_id()
    company = str(listing.get("company") or "").strip()
    title = str(listing.get("title") or "").strip()
    url = str(listing.get("url") or "").strip()
    if not company or not title or not url:
        return ProcessResult(ok=False, reason="missing_required_fields")

    location = (listing.get("location") or None) and str(listing.get("location")).strip()
    description = listing.get("description")
    if description is not None:
        description = str(description).strip() or None
    source = str(listing.get("source") or "manual")
    external_id = listing.get("external_id")
    if external_id is not None:
        external_id = str(external_id)
    work_arrangement = (listing.get("work_arrangement") or None) and str(
        listing.get("work_arrangement")
    ).strip()
    remote_restricted_to = (listing.get("remote_restricted_to") or None) and str(
        listing.get("remote_restricted_to")
    ).strip()

    has_solid_jd = bool(description and len(description) >= SOLID_JD)
    # Auto-enable gates when JD is already in the upload
    run_gates = ((not skip_content_gates) or has_solid_jd) and not bypass_gates

    normalized = normalize_company(company)

    if await company_is_blocked(session, normalized):
        session.add(
            RejectedJob(
                company=company, title=title, url=url, reason="company_blocked", stage="stage1"
            )
        )
        return ProcessResult(ok=False, skipped=True, reason="company_blocked")

    # Multi-profile: only block when THIS persona already applied, unless bypassed
    if not bypass_gates and await company_is_applied(session, pid, normalized):
        session.add(
            RejectedJob(
                company=company,
                title=title,
                url=url,
                reason="company_already_applied",
                stage="stage1",
            )
        )
        return ProcessResult(ok=False, skipped=True, reason="company_already_applied")

    existing_url = await find_job_by_url(session, url)
    if existing_url:
        return ProcessResult(
            ok=False, skipped=True, reason="duplicate_url", job=existing_url
        )

    # Same role, different link. Boards and CSV exports re-list one job under
    # several URLs, so matching the URL alone lets the same posting in twice.
    existing_role = await find_job_by_company_title(session, normalized, title)
    if existing_role:
        return ProcessResult(
            ok=False, skipped=True, reason="duplicate_company_title", job=existing_role
        )

    reject_reason: str | None = None
    reject_stage = "stage1"
    if run_gates:
        sen = check_seniority(title)
        if not sen.ok:
            reject_reason, reject_stage = sen.reason, "stage1"
        else:
            it = check_it_domain(title, description)
            if not it.ok:
                reject_reason, reject_stage = it.reason, "stage1"
            else:
                ru = check_remote_us(
                    location,
                    description,
                    work_arrangement=work_arrangement,
                    remote_restricted_to=remote_restricted_to,
                )
                if not ru.ok:
                    reject_reason, reject_stage = ru.reason, "stage2"

    if reject_reason and not insert_rejects and not has_solid_jd:
        # Legacy path: no JD → only log reject, don't create job row
        session.add(
            RejectedJob(
                company=company,
                title=title,
                url=url,
                reason=reject_reason,
                stage=reject_stage,
            )
        )
        return ProcessResult(ok=False, skipped=True, reason=reject_reason)

    if external_id:
        existing = await session.execute(
            select(Job).where(Job.source == source, Job.external_id == external_id)
        )
        if existing.scalar_one_or_none():
            return ProcessResult(ok=False, skipped=True, reason="duplicate_external_id")
        seen = await session.execute(
            select(JobSourceSeen).where(
                JobSourceSeen.source == source, JobSourceSeen.external_id == external_id
            )
        )
        if seen.scalar_one_or_none():
            return ProcessResult(ok=False, skipped=True, reason="already_seen")
        session.add(JobSourceSeen(source=source, external_id=external_id))

    if reject_reason:
        status = _gate_status(reject_reason)
        notes = f"upload_gate:{reject_reason}"
        session.add(
            RejectedJob(
                company=company,
                title=title,
                url=url,
                reason=reject_reason,
                stage=reject_stage,
            )
        )
    elif has_solid_jd:
        status = JobStatus.JD_READY.value
        notes = None
        if work_arrangement:
            notes = f"work_arrangement={work_arrangement}"
            if remote_restricted_to:
                notes += f"; remote_restricted_to={remote_restricted_to}"
    else:
        status = JobStatus.NEW.value
        notes = None

    company_row = await _get_or_create_company(session, company, normalized)
    seq = await _next_seq(session)

    domains = listing.get("domains")
    if not domains:
        from app.services.job_domains import classify_domains, domains_storage

        domains = domains_storage(classify_domains(title, description))

    job = Job(
        seq_no=seq,
        company=company,
        normalized_company=normalized,
        company_id=company_row.id,
        title=title,
        url=url,
        # Same as url unless the listing carried its own apply link, which only
        # aggregator sources do.
        apply_url=(str(listing.get("apply_url") or "").strip() or url),
        location=location,
        country=country_code(location),
        source=source,
        external_id=external_id,
        description=description,
        status=status,
        persona_id=pid,
        staffing_firm=(listing.get("staffing_firm") or None),
        domains=domains if isinstance(domains, str) else None,
        jd_fetched=has_solid_jd,
        notes=notes,
    )
    session.add(job)
    await session.flush()

    folder = sync_job_folder(
        persona_id=pid,
        seq_no=seq,
        company=company,
        title=title,
        url=url,
        location=location,
        status=status,
        description=description,
        job_id=job.id,
        extra={
            "work_arrangement": work_arrangement,
            "remote_restricted_to": remote_restricted_to,
            "experience_level": listing.get("experience_level"),
        },
    )
    job.folder_path = str(folder)
    await session.flush()
    return ProcessResult(
        ok=not bool(reject_reason),
        job=job,
        skipped=bool(reject_reason),
        reason=reject_reason or "",
        status=status,
    )


async def forget_job_markers(session: AsyncSession, jobs: list[Job]) -> dict[str, int]:
    """Erase the bookkeeping that would stop a deleted job coming back.

    Deleting a job used to leave two traces behind, neither of them reachable
    from the UI. The `job_source_seen` marker is the damaging one: every later
    fetch reads it and skips the listing as `already_seen`, so a job deleted
    once could never be re-added no matter how often the board was fetched.
    341 Jobright listings were stranded that way in a single table.

    `applications` is deliberately left alone. That row records applying to a
    *company*, not to this job — it is unique per persona and company — so
    dropping it would let a later fetch offer an employer already applied to.
    """
    pairs = [(j.source, j.external_id) for j in jobs if j.external_id]
    urls = [j.url for j in jobs if j.url]
    out = {"seen_markers": 0, "rejected_rows": 0}

    for source, external_id in pairs:
        # Source has been written both ways ("Jobright" / "jobright"), so a
        # case-sensitive match would leave half the markers in place.
        result = await session.execute(
            delete(JobSourceSeen).where(
                func.lower(JobSourceSeen.source) == str(source or "").lower(),
                JobSourceSeen.external_id == external_id,
            )
        )
        out["seen_markers"] += int(result.rowcount or 0)

    if urls:
        result = await session.execute(
            delete(RejectedJob).where(RejectedJob.url.in_(urls))
        )
        out["rejected_rows"] += int(result.rowcount or 0)
    return out


async def prune_orphan_seen_markers(session: AsyncSession) -> int:
    """Drop seen-markers whose job is gone, and report how many.

    Markers are only ever written beside a job row (a listing rejected before
    that point returns earlier and is never marked), so a marker with no job
    means the job was deleted while the marker outlived it. Clearing them lets
    those listings be fetched into the table again.
    """
    live = select(Job.external_id).where(Job.external_id.is_not(None))
    result = await session.execute(
        delete(JobSourceSeen).where(JobSourceSeen.external_id.not_in(live))
    )
    return int(result.rowcount or 0)
