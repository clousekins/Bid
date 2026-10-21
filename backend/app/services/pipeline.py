from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import JobStatus
from app.db.job_tables import (
    allocate_job_id,
    ensure_job_table,
    job_model,
    persona_accepts_country,
)
from app.db.models import (
    Application,
    Company,
    JobRow,
    JobSourceSeen,
    RejectedJob,
    as_utc,
    utcnow,
)
from app.services.employer_conflicts import persona_employers
from app.services.folder_writer import sync_job_folder
from app.services.job_country import country_code
from app.services.persona import all_persona_ids, get_active_persona_id, load_persona
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


def is_stale_duplicate(job: JobRow) -> bool:
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


@dataclass
class ProcessResult:
    ok: bool
    job: JobRow | None = None
    skipped: bool = False
    reason: str = ""
    status: str | None = None


async def _next_seq(session: AsyncSession, persona_id: str) -> int:
    """The next sequence number *within this persona's own table*."""
    model = job_model(persona_id)
    result = await session.execute(select(func.coalesce(func.max(model.seq_no), 0)))
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
    """Has this persona already applied at this company, within the window?

    Two records now say so: an ``Application`` row (company-level, durable) and
    the persona's own applied job row in ``jobs__{persona}``. A rejected listing
    counts for neither — rejecting one writes its application off, so the company
    reopens for the next job from there. Only applies inside
    ``APPLIED_WINDOW_DAYS`` count.
    """
    model = job_model(persona_id)
    rejected = JobStatus.REJECTED.value
    cutoff = _applied_cutoff()

    # Application: company-level. One tied to a now-rejected job is written off
    # with it; a standalone one is the whole record, so it still counts.
    result = await session.execute(
        select(Application.id)
        .outerjoin(model, model.id == Application.job_id)
        .where(
            Application.persona_id == persona_id,
            Application.normalized_company == normalized,
            Application.status == "applied",
            Application.created_at >= cutoff,
            or_(Application.job_id.is_(None), model.status != rejected),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return True

    # The persona's own applied row at this company.
    result = await session.execute(
        select(model.id)
        .where(
            model.normalized_company == normalized,
            model.status == JobStatus.APPLIED.value,
            func.coalesce(model.applied_at, model.updated_at) >= cutoff,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def applied_scope(
    session: AsyncSession, persona_id: str
) -> tuple[set[str], set[int]]:
    """Companies this persona has applied at, and the jobs it applied to.

    Returns (companies, job_ids). Applying at a company closes its *other*
    openings to this persona in the list, but the one listing it actually
    applied to stays visible — so job_ids is the exception the company set is
    read against. Same records and window as :func:`company_is_applied`.
    """
    model = job_model(persona_id)
    rejected = JobStatus.REJECTED.value
    cutoff = _applied_cutoff()
    companies: set[str] = set()
    job_ids: set[int] = set()

    rows = await session.execute(
        select(Application.normalized_company, Application.job_id)
        .outerjoin(model, model.id == Application.job_id)
        .where(
            Application.persona_id == persona_id,
            Application.status == "applied",
            Application.created_at >= cutoff,
            or_(Application.job_id.is_(None), model.status != rejected),
        )
    )
    for company, job_id in rows:
        if company:
            companies.add(company)
        if job_id is not None:
            job_ids.add(int(job_id))

    rows = await session.execute(
        select(model.id, model.normalized_company).where(
            model.status == JobStatus.APPLIED.value,
            func.coalesce(model.applied_at, model.updated_at) >= cutoff,
        )
    )
    for job_id, company in rows:
        job_ids.add(int(job_id))
        if company:
            companies.add(company)

    return companies, job_ids


async def explicit_disabled_ids(session: AsyncSession, persona_id: str) -> set[int]:
    """Jobs this persona turned off by hand (``enabled = False``)."""
    if not persona_id:
        return set()
    model = job_model(persona_id)
    rows = await session.execute(select(model.id).where(model.enabled.is_(False)))
    return {int(r[0]) for r in rows}


def auto_disabled(
    job: JobRow, applied_company_set: set[str], applied_job_ids: set[int]
) -> bool:
    """A company's other opening, auto-disabled because the persona applied there.

    True when the job is at a company the persona applied to (within the 30-day
    window applied_scope enforces) but is not itself the one it applied to.
    """
    return (
        job.normalized_company in applied_company_set
        and job.id not in applied_job_ids
    )


def effective_disabled(
    job: JobRow,
    applied_company_set: set[str],
    applied_job_ids: set[int],
) -> tuple[bool, str | None]:
    """Is this job disabled for its persona, and why. An explicit choice wins.

    Reads the row's folded tri-state ``enabled``: ``False`` = off by hand,
    ``True`` = forced on, ``None`` = follow the automatic company-applied rule.
    """
    if job.enabled is False:
        return True, "manual"
    if job.enabled is True:
        return False, None
    if auto_disabled(job, applied_company_set, applied_job_ids):
        return True, "company_applied"
    return False, None


async def disabled_job_ids_for(session: AsyncSession, persona_id: str) -> set[int]:
    """Every job id disabled for this persona — applied-company siblings and
    hand-disabled rows, minus any forced back on. Keeps the resume-build batch
    off disabled jobs."""
    if not persona_id:
        return set()
    model = job_model(persona_id)
    companies, applied_ids = await applied_scope(session, persona_id)
    rows = await session.execute(
        select(model.id, model.normalized_company, model.enabled)
    )
    out: set[int] = set()
    for jid, comp, enabled in rows:
        jid = int(jid)
        if enabled is False:
            out.add(jid)
        elif enabled is True:
            continue
        elif comp in companies and jid not in applied_ids:
            out.add(jid)
    return out


async def is_disabled_for(
    session: AsyncSession, job: JobRow, persona_id: str | None
) -> tuple[bool, str | None]:
    """Disabled state of one job for its persona (for the single-job view)."""
    if not persona_id:
        return False, None
    if job.enabled is False:
        return True, "manual"
    if job.enabled is True:
        return False, None
    companies, applied_ids = await applied_scope(session, persona_id)
    auto = auto_disabled(job, companies, applied_ids)
    return (auto, "company_applied" if auto else None)


async def find_job_by_url(
    session: AsyncSession,
    persona_id: str,
    url: str,
    *,
    exclude_ids: set[int] | None = None,
) -> JobRow | None:
    model = job_model(persona_id)
    canon = canonicalize_url(url)
    if not canon:
        return None
    exclude = exclude_ids or set()
    variants = {url.strip(), url.strip().rstrip("/"), canon}
    where = [or_(model.url.in_(variants), model.apply_url.in_(variants))]
    if exclude:
        where.append(model.id.notin_(exclude))
    row = (
        await session.execute(
            select(model).where(*where).order_by(model.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if row:
        return row
    jobs = (
        await session.execute(select(model).order_by(model.id.desc()).limit(400))
    ).scalars().all()
    for job in jobs:
        if job.id in exclude:
            continue
        if canonicalize_url(job.url or "") == canon or canonicalize_url(job.apply_url or "") == canon:
            return job
    return None


async def find_job_by_company_title(
    session: AsyncSession, persona_id: str, normalized_company: str, title: str
) -> JobRow | None:
    """An existing listing in this persona's table for the same company and role."""
    model = job_model(persona_id)
    key = normalize_title(title)
    if not normalized_company or not key:
        return None
    rows = (
        await session.execute(
            select(model)
            .where(model.normalized_company == normalized_company)
            .order_by(model.id.desc())
        )
    ).scalars().all()
    for job in rows:
        if normalize_title(job.title or "") == key:
            return job
    return None


async def find_recent_job_by_company(
    session: AsyncSession,
    persona_id: str,
    normalized_company: str,
    *,
    exclude_ids: set[int] | None = None,
) -> JobRow | None:
    """The newest non-rejected listing from this company in this persona's table
    still inside the window.

    One job per company per REINSERT_STALE_DAYS, per persona: a company that
    already has a non-rejected listing in this table takes no more until it ages
    out. A rejected listing is written off, so it frees the company. exclude_ids
    drops rows the persona disabled by hand.
    """
    model = job_model(persona_id)
    if not normalized_company:
        return None
    where = [
        model.normalized_company == normalized_company,
        model.status != JobStatus.REJECTED.value,
    ]
    if exclude_ids:
        where.append(model.id.notin_(exclude_ids))
    row = (
        await session.execute(
            select(model).where(*where).order_by(model.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if row is not None and not is_stale_duplicate(row):
        return row
    return None


def _is_own_employer(persona_id: str, normalized: str) -> bool:
    """Is this company one the persona's own resume claims as an employer?

    load_persona is mtime-cached, so this is a cheap lookup inside the ingest
    loop. A persona that fails to load simply blocks nothing.
    """
    if not normalized:
        return False
    try:
        return normalized in persona_employers(load_persona(persona_id))
    except Exception:  # noqa: BLE001
        return False


async def company_is_federal(session: AsyncSession, normalized: str) -> bool:
    result = await session.execute(
        select(Company.id).where(Company.normalized_name == normalized, Company.federal.is_(True))
    )
    return result.first() is not None


async def federal_companies(session: AsyncSession, normalized: set[str]) -> set[str]:
    """Which of these normalized company names are marked federal."""
    if not normalized:
        return set()
    result = await session.execute(
        select(Company.normalized_name).where(
            Company.normalized_name.in_(normalized), Company.federal.is_(True)
        )
    )
    return {str(r[0]) for r in result.all()}


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
    """Insert one job listing as a copy in one persona's own table.

    When description is present and gates are enabled, classify as jd_ready or
    rejected_* at insert time. Deep-scan remains available for status=new /
    scan_failed rows. The caller decides which personas get a copy
    (:func:`ingest_listing_for_personas`); this writes exactly one.
    """
    del fetch_jd
    pid = persona_id or get_active_persona_id()
    await ensure_job_table(pid)
    model = job_model(pid)
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

    # A company marked federal from a job's detail is not ingested again, for
    # any persona.
    if await company_is_federal(session, normalized):
        session.add(
            RejectedJob(
                company=company, title=title, url=url, reason="federal_company", stage="stage1"
            )
        )
        return ProcessResult(ok=False, skipped=True, reason="federal_company")

    # A persona cannot apply to a company it already works for (or a sister brand
    # of it) — the resume claims that employer. Skip those at ingest so they never
    # enter this persona's own table.
    if not bypass_gates and _is_own_employer(pid, normalized):
        session.add(
            RejectedJob(
                company=company, title=title, url=url, reason="own_employer", stage="stage1"
            )
        )
        return ProcessResult(ok=False, skipped=True, reason="own_employer")

    # Per-person gate: skip only if *this* persona has already applied at the
    # company within the window. Another persona's apply does not close it here —
    # each person owns their own copy.
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

    # Ingestion ignores jobs this persona disabled by hand: a disabled listing no
    # longer holds its URL or company against a fresh one.
    disabled_exclude = await explicit_disabled_ids(session, pid)

    # A match ingested more than REINSERT_STALE_DAYS ago is let through as a
    # fresh listing — the old row stays and a new one is added at the end.
    existing_url = await find_job_by_url(session, pid, url, exclude_ids=disabled_exclude)
    if existing_url and not is_stale_duplicate(existing_url):
        return ProcessResult(
            ok=False, skipped=True, reason="duplicate_url", job=existing_url
        )

    # One job per company per the re-insert window (per persona).
    recent_company = await find_recent_job_by_company(
        session, pid, normalized, exclude_ids=disabled_exclude
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

    job_country = country_code(location)

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
    seq = await _next_seq(session, pid)
    job_id = await allocate_job_id(session, pid, normalized)

    domains = listing.get("domains")
    if not domains:
        from app.services.job_domains import classify_domains, domains_storage

        domains = domains_storage(classify_domains(title, description))

    from app.services.salary_extract import extract_salary

    salary = (str(listing.get("salary") or "").strip() or None) or extract_salary(
        description
    )

    job = model(
        id=job_id,
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


async def ingest_listing_for_personas(
    session: AsyncSession,
    listing: dict[str, Any],
    *,
    persona_ids: list[str],
    skip_content_gates: bool = False,
    insert_rejects: bool = False,
    bypass_gates: bool = False,
) -> dict[str, ProcessResult]:
    """Ingest one listing as a separate copy for each selected, eligible persona.

    Country routing filters the set: a Brazil listing lands only in Bruno's
    table, a US listing in the US-eligible selected personas. Returns the
    per-persona result keyed by persona id (eligible personas only).
    """
    country = country_code(
        (listing.get("location") or None) and str(listing.get("location")).strip()
    )
    results: dict[str, ProcessResult] = {}
    for pid in persona_ids:
        if not persona_accepts_country(pid, country):
            continue
        results[pid] = await process_listing(
            session,
            listing,
            persona_id=pid,
            skip_content_gates=skip_content_gates,
            insert_rejects=insert_rejects,
            bypass_gates=bypass_gates,
        )
    return results


async def forget_job_markers(session: AsyncSession, jobs: list[JobRow]) -> dict[str, int]:
    """Erase the bookkeeping that would stop a deleted job coming back.

    Deleting a job used to leave two traces behind. The `job_source_seen` marker
    is the damaging one: every later fetch reads it and skips the listing as
    `already_seen`, so a job deleted once could never be re-added. `applications`
    is deliberately left alone — it records applying to a *company*, not this job.
    """
    pairs = [(j.source, j.external_id) for j in jobs if j.external_id]
    urls = [j.url for j in jobs if j.url]
    out = {"seen_markers": 0, "rejected_rows": 0}

    for source, external_id in pairs:
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


async def live_external_ids(session: AsyncSession) -> set[str]:
    """Every external_id still present across all persona tables."""
    live: set[str] = set()
    for pid in all_persona_ids():
        model = job_model(pid)
        rows = await session.execute(
            select(model.external_id).where(model.external_id.is_not(None))
        )
        live.update(str(r[0]) for r in rows if r[0] is not None)
    return live


async def prune_orphan_seen_markers(session: AsyncSession) -> int:
    """Drop seen-markers whose job is gone (across every persona table)."""
    live = await live_external_ids(session)
    result = await session.execute(
        delete(JobSourceSeen).where(JobSourceSeen.external_id.not_in(live))
        if live
        else delete(JobSourceSeen)
    )
    return int(result.rowcount or 0)
