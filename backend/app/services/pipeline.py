from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.enums import JobStatus, PackageStatus
from app.db.models import (
    Application,
    Company,
    Job,
    JobDisable,
    JobPackage,
    JobSourceSeen,
    RejectedJob,
    as_utc,
    utcnow,
)
from app.services.folder_writer import sync_job_folder
from app.services.job_country import country_code
from app.services.persona import get_active_persona_id
from app.validators.dedupe import canonicalize_url, normalize_company, normalize_title
from app.validators.it_domain import check_it_domain
from app.validators.remote_us import check_remote_us
from app.validators.seniority import check_seniority

SOLID_JD = 50

# A duplicate whose existing copy was ingested longer ago than this is treated
# as a fresh listing: the old row is kept, and the job is inserted again at the
# end with the next sequence number. Re-postings older than this are usually a
# new hiring round, and the old copy's resume and pipeline state are stale.
REINSERT_STALE_DAYS = 7


def is_stale_duplicate(job: Job) -> bool:
    """True when this existing match is old enough to re-insert rather than reuse."""
    created = getattr(job, "created_at", None)
    if created is None:
        return False
    return as_utc(created) < utcnow() - timedelta(days=REINSERT_STALE_DAYS)

# An apply older than this no longer counts as "applied at this company". Past
# it the company reopens: ingest takes its listings again, and the job list
# stops hiding its other openings. So a company applied to long ago comes back.
APPLIED_WINDOW_DAYS = 30


def _applied_cutoff():
    """Applies on or after this instant still count; older ones are ignored."""
    return utcnow() - timedelta(days=APPLIED_WINDOW_DAYS)

# The profiles that make up the apply crew. Ingest is closed to a company only
# once every one of them has already applied there — so a listing is skipped at
# ingest when both Alex and Brian have applied to that company, and let in
# otherwise. (The job list's hide is separate and keyed on the viewing profile,
# not this crew.) Edit this list to change the crew.
APPLY_GROUP: tuple[str, ...] = ("alex_mak", "brian_gou")


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

    Only applies from the last APPLIED_WINDOW_DAYS count. One older than that is
    treated as spent: the company reopens, so a listing applied to long ago no
    longer blocks ingest or hides the company's other openings.
    """
    rejected = JobStatus.REJECTED.value
    cutoff = _applied_cutoff()

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
            Application.created_at >= cutoff,
            or_(Application.job_id.is_(None), Job.status != rejected),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return True

    # The listing's own status needs no exclusion: a job is applied or
    # rejected, never both. updated_at is the best apply time it carries.
    result = await session.execute(
        select(Job.id)
        .where(
            Job.persona_id == persona_id,
            Job.normalized_company == normalized,
            Job.status == JobStatus.APPLIED.value,
            Job.updated_at >= cutoff,
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
            func.coalesce(JobPackage.applied_at, JobPackage.updated_at) >= cutoff,
            Job.normalized_company == normalized,
            Job.status != rejected,
        )
        .limit(1)
    )
    return pkg.scalar_one_or_none() is not None


async def group_company_applied(
    session: AsyncSession, normalized: str, crew: tuple[str, ...] = APPLY_GROUP
) -> bool:
    """Has every profile in the crew already applied at this company?

    Ingest is gated on the crew as a whole, not on the fetching profile: a
    listing is only worth skipping once none of them could still use it. Until
    then it is kept even if the profile doing the fetch is itself already
    applied. company_is_applied answers it per profile and already discounts an
    apply written off with a rejected listing, so the company reopens for the
    crew the moment any one of them is freed up again.

    The crew is the US pair (Alex and Brian) by default, but a Brazil listing
    passes crew=("bruno_raphael",), since Brazil jobs are Bruno's.
    """
    for persona_id in crew:
        if not await company_is_applied(session, persona_id, normalized):
            return False
    return True


async def applied_scope(
    session: AsyncSession, persona_id: str
) -> tuple[set[str], set[int]]:
    """Companies this profile has applied at, and the jobs it applied to.

    Returns (companies, job_ids). Applying at a company closes its *other*
    openings to this profile in the list, but the one listing the profile
    actually applied to stays visible — so job_ids is the exception the company
    set is read against. Gathered in a few indexed passes rather than a
    correlated subquery per row, the way the own-employers hide is. Same three
    records as company_is_applied, and the same rule that a rejected listing
    writes its own applies off.
    """
    rejected = JobStatus.REJECTED.value
    applied = PackageStatus.APPLIED.value
    # Only applies from the last APPLIED_WINDOW_DAYS count; an older one leaves
    # the company unhidden, so its openings drop back into the list.
    cutoff = _applied_cutoff()
    companies: set[str] = set()
    job_ids: set[int] = set()

    # A standalone Application (no job) is the whole record, so it still closes
    # the company; one tied to a rejected listing is written off with it.
    rows = await session.execute(
        select(Application.normalized_company, Application.job_id)
        .outerjoin(Job, Job.id == Application.job_id)
        .where(
            Application.persona_id == persona_id,
            Application.status == "applied",
            Application.created_at >= cutoff,
            or_(Application.job_id.is_(None), Job.status != rejected),
        )
    )
    for company, job_id in rows:
        if company:
            companies.add(company)
        if job_id is not None:
            job_ids.add(job_id)

    rows = await session.execute(
        select(Job.id, Job.normalized_company).where(
            Job.persona_id == persona_id,
            Job.status == JobStatus.APPLIED.value,
            Job.updated_at >= cutoff,
        )
    )
    for job_id, company in rows:
        job_ids.add(job_id)
        if company:
            companies.add(company)

    rows = await session.execute(
        select(Job.id, Job.normalized_company)
        .join(JobPackage, JobPackage.job_id == Job.id)
        .where(
            JobPackage.persona_id == persona_id,
            JobPackage.status == applied,
            func.coalesce(JobPackage.applied_at, JobPackage.updated_at) >= cutoff,
            Job.status != rejected,
        )
    )
    for job_id, company in rows:
        job_ids.add(job_id)
        if company:
            companies.add(company)

    return companies, job_ids


async def profile_disable_overrides(
    session: AsyncSession, persona_id: str
) -> dict[int, bool]:
    """job_id -> this profile's explicit choice (True=off, False=forced back on)."""
    rows = await session.execute(
        select(JobDisable.job_id, JobDisable.disabled).where(
            JobDisable.persona_id == persona_id
        )
    )
    return {int(jid): bool(d) for jid, d in rows}


async def explicit_disabled_ids(session: AsyncSession, persona_id: str) -> set[int]:
    """Jobs this profile turned off by hand (override disabled=True)."""
    if not persona_id:
        return set()
    rows = await session.execute(
        select(JobDisable.job_id).where(
            JobDisable.persona_id == persona_id, JobDisable.disabled.is_(True)
        )
    )
    return {int(r[0]) for r in rows}


def auto_disabled(
    job: Job, applied_company_set: set[str], applied_job_ids: set[int]
) -> bool:
    """A company's other opening, auto-disabled because the profile applied there.

    True when the job is at a company the profile has applied to (within the
    30-day window applied_scope enforces) but is not itself one it applied to.
    """
    return (
        job.normalized_company in applied_company_set
        and job.id not in applied_job_ids
    )


def effective_disabled(
    job: Job,
    applied_company_set: set[str],
    applied_job_ids: set[int],
    overrides: dict[int, bool],
) -> tuple[bool, str | None]:
    """Is this job disabled for the profile, and why. An explicit override wins."""
    override = overrides.get(job.id)
    if override is True:
        return True, "manual"
    if override is False:
        return False, None
    if auto_disabled(job, applied_company_set, applied_job_ids):
        return True, "company_applied"
    return False, None


async def disabled_job_ids_for(session: AsyncSession, persona_id: str) -> set[int]:
    """Every job id disabled for this profile — applied-company siblings and
    hand-disabled jobs, minus any the profile forced back on. Used to keep the
    resume-build batch off disabled jobs."""
    if not persona_id:
        return set()
    companies, applied_ids = await applied_scope(session, persona_id)
    overrides = await profile_disable_overrides(session, persona_id)
    effective: set[int] = set()
    if companies:
        clauses = [Job.normalized_company.in_(companies)]
        if applied_ids:
            clauses.append(Job.id.notin_(applied_ids))
        rows = await session.execute(select(Job.id).where(*clauses))
        for (jid,) in rows:
            if overrides.get(int(jid)) is not False:  # auto, unless forced on
                effective.add(int(jid))
    for jid, dis in overrides.items():
        if dis:
            effective.add(jid)
        else:
            effective.discard(jid)
    return effective


async def is_disabled_for(
    session: AsyncSession, job: Job, persona_id: str | None
) -> tuple[bool, str | None]:
    """Disabled state of one job for one profile (for the single-job view)."""
    if not persona_id:
        return False, None
    override = (
        await session.execute(
            select(JobDisable.disabled).where(
                JobDisable.job_id == job.id, JobDisable.persona_id == persona_id
            )
        )
    ).scalar_one_or_none()
    if override is not None:
        return (bool(override), "manual" if override else None)
    companies, applied_ids = await applied_scope(session, persona_id)
    auto = auto_disabled(job, companies, applied_ids)
    return (auto, "company_applied" if auto else None)


async def find_job_by_url(
    session: AsyncSession, url: str, *, exclude_ids: set[int] | None = None
) -> Job | None:
    canon = canonicalize_url(url)
    if not canon:
        return None
    exclude = exclude_ids or set()
    variants = {url.strip(), url.strip().rstrip("/"), canon}
    where = [or_(Job.url.in_(variants), Job.apply_url.in_(variants))]
    if exclude:
        where.append(Job.id.notin_(exclude))
    row = (
        await session.execute(
            select(Job)
            .options(selectinload(Job.packages))
            .where(*where)
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
        if job.id in exclude:
            continue
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


async def find_recent_job_by_company(
    session: AsyncSession, normalized_company: str, *, exclude_ids: set[int] | None = None
) -> Job | None:
    """The newest non-rejected listing from this company still inside the window.

    One job per company per REINSERT_STALE_DAYS: any such match (regardless of
    role or domain) holds the company closed to ingest until it ages out. A
    rejected listing is written off, so it frees the company and is skipped here.
    exclude_ids drops jobs the ingesting profile has disabled, so a disabled
    listing no longer blocks a fresh one.
    """
    if not normalized_company:
        return None
    where = [
        Job.normalized_company == normalized_company,
        Job.status != JobStatus.REJECTED.value,
    ]
    if exclude_ids:
        where.append(Job.id.notin_(exclude_ids))
    row = (
        await session.execute(
            select(Job)
            .options(selectinload(Job.packages))
            .where(*where)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is not None and not is_stale_duplicate(row):
        return row
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

    # Brazil listings are Bruno's; everywhere else is the US pair. A Brazil job
    # is closed to ingest once Bruno has applied at the company; any other job
    # once the whole US crew (Alex and Brian) has. Until then at least one of
    # the crew can still use the listing, so it is kept — even when the profile
    # doing the fetch is itself already applied.
    job_country = country_code(location)
    crew = ("bruno_raphael",) if job_country == "BR" else APPLY_GROUP
    if (
        not bypass_gates
        and await group_company_applied(session, normalized, crew=crew)
    ):
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

    # Ingestion does not care about jobs this profile disabled by hand: a
    # disabled listing no longer holds its URL or its company against a fresh
    # one, so a new opening comes in instead of being deduped away.
    disabled_exclude = await explicit_disabled_ids(session, pid)

    # A match ingested more than REINSERT_STALE_DAYS ago is let through as a
    # fresh listing — the old row stays and a new one is added at the end.
    existing_url = await find_job_by_url(session, url, exclude_ids=disabled_exclude)
    if existing_url and not is_stale_duplicate(existing_url):
        return ProcessResult(
            ok=False, skipped=True, reason="duplicate_url", job=existing_url
        )

    # One job per company per the re-insert window: a company that already has a
    # non-rejected listing from the last REINSERT_STALE_DAYS takes no more — any
    # role or domain — until that listing ages out. A rejected job frees it.
    recent_company = await find_recent_job_by_company(
        session, normalized, exclude_ids=disabled_exclude
    )
    if recent_company:
        return ProcessResult(
            ok=False, skipped=True, reason="duplicate_company", job=recent_company
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
        # Ingest never skips on the board id. URL and company+title dedup above
        # still catch true repeats. The seen-marker is still recorded (once, so
        # its unique constraint holds), because the Jobright fetch reads it to
        # pull only new listings each run — it just no longer blocks an insert.
        already_marked = (
            await session.execute(
                select(JobSourceSeen.id).where(
                    JobSourceSeen.source == source,
                    JobSourceSeen.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if already_marked is None:
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

    from app.services.salary_extract import extract_salary

    salary = (str(listing.get("salary") or "").strip() or None) or extract_salary(
        description
    )

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
        country=job_country,
        source=source,
        external_id=external_id,
        description=description,
        salary=salary,
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
