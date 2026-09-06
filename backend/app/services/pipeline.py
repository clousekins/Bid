from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.enums import JobStatus, PackageStatus
from app.db.models import Application, Company, Job, JobPackage, JobSourceSeen, RejectedJob
from app.services.folder_writer import sync_job_folder
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
    result = await session.execute(
        select(Application.id).where(
            Application.persona_id == persona_id,
            Application.normalized_company == normalized,
            Application.status == "applied",
        )
    )
    if result.scalar_one_or_none() is not None:
        return True
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
        apply_url=url,
        location=location,
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
