from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.enums import JobStatus
from app.db.models import Job
from app.services.employer_resolve import apply_employer_to_job_fields, resolve_employer
from app.services.folder_writer import job_folder_name, remove_job_folder, sync_job_folder
from app.services.jd_fetch import FetchResult, fetch_jd_http
from app.services.jd_fetch_browser import fetch_jd_browser
from app.services.jd_extract import extract_job_fields_from_jd
from app.services.pipeline import (
    _get_or_create_company,
    company_is_applied,
    company_is_blocked,
    find_job_by_url,
)
from app.validators.dedupe import canonicalize_url, normalize_company
from app.validators.remote_us import check_remote_us


SOLID_JD_CHARS = 400


def has_solid_jd(job: Job) -> bool:
    desc = job.description or ""
    return bool(job.jd_fetched and len(desc) >= SOLID_JD_CHARS)


async def fetch_description(url: str) -> tuple[FetchResult, str]:
    """HTTP first, then headless browser. Same order as Deep-scan."""
    result = await fetch_jd_http(url)
    http_reason = result.reason if not result.ok else "ok"
    if not result.ok:
        browser = await fetch_jd_browser(url)
        if browser.ok:
            return browser, http_reason
        return FetchResult(
            ok=False,
            text=browser.text or result.text,
            reason=browser.reason or result.reason or "fetch_failed",
        ), http_reason
    return result, http_reason


def _drop_stale_company_folder(job: Job, old_company: str, new_folder) -> None:
    if not job.seq_no or old_company == job.company:
        return
    stale = get_settings().output_root / job.persona_id / job_folder_name(job.seq_no, old_company)
    try:
        if stale.resolve() != new_folder.resolve():
            remove_job_folder(stale)
    except OSError:
        remove_job_folder(stale)


async def _apply_resolved_employer(
    session: AsyncSession, job: Job, jd_text: str
) -> dict:
    """Resolve client company from JD; update job; gate on applied/blocked client."""
    resolved = await resolve_employer(
        listing_company=job.company,
        title=job.title,
        url=job.apply_url or job.url or "",
        jd=jd_text,
    )
    change = apply_employer_to_job_fields(job, resolved)
    if not change.get("changed"):
        return {"employer": change}

    company_row = await _get_or_create_company(
        session, job.company, job.normalized_company
    )
    job.company_id = company_row.id

    if await company_is_blocked(session, job.normalized_company):
        job.status = JobStatus.BLOCKED.value
        job.notes = f"gate:client_company_blocked:{job.company}"
        return {"employer": change, "gate": "company_blocked"}

    if await company_is_applied(session, job.persona_id, job.normalized_company):
        job.status = JobStatus.SKIPPED.value
        job.notes = f"gate:client_already_applied:{job.company}"
        return {"employer": change, "gate": "company_already_applied"}

    return {"employer": change}


async def deep_scan_job(session: AsyncSession, job: Job, *, force: bool = False) -> dict:
    if not force and has_solid_jd(job):
        return {
            "job_id": job.id,
            "seq_no": job.seq_no,
            "status": job.status,
            "skipped": True,
            "reason": "already_has_jd",
        }

    url = job.apply_url or job.url
    result, http_reason = await fetch_description(url)

    if not result.ok or len(result.text) < 200:
        reason = result.reason or "fetch_failed"
        job.notes = f"deep_scan_failed:{reason} (http={http_reason})"
        job.status = JobStatus.SCAN_FAILED.value
        await session.flush()
        return {
            "job_id": job.id,
            "seq_no": job.seq_no,
            "status": job.status,
            "ok": False,
            "reason": reason,
            "note": job.notes,
        }

    old_company = job.company
    employer_meta = await _apply_resolved_employer(session, job, result.text)
    gate_blocked = employer_meta.get("gate")

    job.description = result.text
    job.jd_fetched = True

    if gate_blocked:
        folder = sync_job_folder(
            persona_id=job.persona_id,
            seq_no=job.seq_no or 0,
            company=job.company,
            title=job.title,
            url=job.url,
            location=job.location,
            status=job.status,
            description=result.text,
            job_id=job.id,
            extra={"staffing_firm": job.staffing_firm, "employer_resolve": employer_meta},
        )
        _drop_stale_company_folder(job, old_company, folder)
        job.folder_path = str(folder)
        await session.flush()
        return {
            "job_id": job.id,
            "seq_no": job.seq_no,
            "ok": False,
            "reason": gate_blocked,
            "status": job.status,
            "employer": employer_meta.get("employer"),
        }

    gate = check_remote_us(job.location, result.text)
    if not gate.ok:
        job.status = (
            JobStatus.REJECTED_ONSITE.value
            if gate.reason in {"hybrid_or_onsite", "not_remote", "not_us", "non_us_only"}
            else JobStatus.NEEDS_MANUAL.value
        )
        job.notes = f"gate:{gate.reason}"
        folder = sync_job_folder(
            persona_id=job.persona_id,
            seq_no=job.seq_no or 0,
            company=job.company,
            title=job.title,
            url=job.url,
            location=job.location,
            status=job.status,
            description=result.text,
            job_id=job.id,
            extra={"staffing_firm": job.staffing_firm, "employer_resolve": employer_meta},
        )
        _drop_stale_company_folder(job, old_company, folder)
        job.folder_path = str(folder)
        await session.flush()
        return {
            "job_id": job.id,
            "seq_no": job.seq_no,
            "ok": False,
            "reason": gate.reason,
            "status": job.status,
            "employer": employer_meta.get("employer"),
        }

    job.notes = None
    if job.status in {
        JobStatus.NEW.value,
        JobStatus.JD_READY.value,
        JobStatus.SCAN_FAILED.value,
    }:
        job.status = JobStatus.JD_READY.value
    folder = sync_job_folder(
        persona_id=job.persona_id,
        seq_no=job.seq_no or 0,
        company=job.company,
        title=job.title,
        url=job.url,
        location=job.location,
        status=job.status,
        description=result.text,
        job_id=job.id,
        extra={"staffing_firm": job.staffing_firm, "employer_resolve": employer_meta},
    )
    _drop_stale_company_folder(job, old_company, folder)
    job.folder_path = str(folder)
    await session.flush()
    return {
        "job_id": job.id,
        "seq_no": job.seq_no,
        "ok": True,
        "status": job.status,
        "chars": len(result.text),
        "employer": employer_meta.get("employer"),
    }


async def deep_scan_range(
    session: AsyncSession,
    *,
    persona_id: str,
    seq_from: int,
    seq_to: int,
    limit: int = 50,
    cancel_check=None,
) -> dict:
    q = (
        select(Job)
        .where(
            Job.persona_id == persona_id,
            Job.seq_no >= seq_from,
            Job.seq_no <= seq_to,
            Job.status.in_(
                [
                    JobStatus.NEW.value,
                    JobStatus.JD_READY.value,
                    JobStatus.SCAN_FAILED.value,
                ]
            ),
        )
        .order_by(Job.seq_no.asc())
        .limit(limit)
    )
    rows = (await session.execute(q)).scalars().all()
    results = []
    for job in rows:
        if cancel_check and await cancel_check():
            return {"cancelled": True, "results": results}
        results.append(await deep_scan_job(session, job))
        await session.commit()
    return {"cancelled": False, "results": results, "count": len(results)}


async def preview_url_fetch(
    session: AsyncSession,
    *,
    url: str,
    persona_id: str,
    job: Job | None = None,
) -> dict:
    """Fetch a JD without always inserting. Same HTTP→browser path as Deep-scan.

    Returns extracted fields plus blockers (cannot add) and warnings (can continue).
    """
    url = (url or "").strip()
    if not url or not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "reason": "url_required", "blockers": ["url_required"], "warnings": []}

    existing = job or await find_job_by_url(session, url)
    if existing:
        explicit = job is not None
        if explicit or not has_solid_jd(existing):
            scan = await deep_scan_job(session, existing, force=True)
        else:
            scan = {"ok": True, "skipped": True, "reason": "already_has_jd"}
        company = existing.company
        normalized = existing.normalized_company or normalize_company(company)
        blockers: list[str] = []
        warnings: list[str] = ["duplicate_url"]
        if scan.get("skipped"):
            warnings.append("already_has_jd")
        if await company_is_blocked(session, normalized):
            blockers.append("company_blocked")
        elif await company_is_applied(session, persona_id, normalized):
            blockers.append("company_already_applied")
        if existing.status in {JobStatus.REJECTED_ONSITE.value}:
            warnings.append("hybrid_or_onsite")
        if not scan.get("ok") and scan.get("reason") and scan.get("reason") not in blockers:
            if scan.get("reason") in {"company_blocked", "company_already_applied"}:
                blockers.append(scan["reason"])
            elif scan.get("reason") not in {"already_has_jd"}:
                warnings.append(str(scan["reason"]))
        return {
            "ok": not blockers and (scan.get("ok") or scan.get("skipped") or bool(existing.description)),
            "reason": (blockers[0] if blockers else (scan.get("reason") or "duplicate_url")),
            "existing": True,
            "job_id": existing.id,
            "company": existing.company,
            "title": existing.title,
            "url": existing.url,
            "location": existing.location or "Remote, US",
            "description": existing.description or "",
            "staffing_firm": existing.staffing_firm,
            "blockers": blockers,
            "warnings": warnings,
            "fetch_via": "existing_job",
        }

    result, http_reason = await fetch_description(url)
    if not result.ok or len(result.text) < 200:
        reason = result.reason or "fetch_failed"
        return {
            "ok": False,
            "reason": reason,
            "existing": False,
            "description": result.text or "",
            "url": url,
            "blockers": [reason],
            "warnings": [],
            "fetch_via": f"http={http_reason}",
        }

    extracted = await extract_job_fields_from_jd(result.text)
    company = (extracted.get("company") or "").strip() or "Unknown Company"
    title = (extracted.get("title") or "").strip() or "Unknown Role"
    location = (extracted.get("location") or "").strip() or "Remote, US"
    resolved = await resolve_employer(
        listing_company=company,
        title=title,
        url=url,
        jd=result.text,
    )
    if resolved.company:
        company = resolved.company
    staffing_firm = resolved.source_name or None
    normalized = normalize_company(company)
    blockers = []
    warnings = []
    if http_reason not in {"ok", ""}:
        warnings.append(f"http_{http_reason}")
    if staffing_firm and staffing_firm != company:
        warnings.append("staffing_firm_resolved")
    if await company_is_blocked(session, normalized):
        blockers.append("company_blocked")
    elif await company_is_applied(session, persona_id, normalized):
        blockers.append("company_already_applied")
    gate = check_remote_us(location, result.text)
    if not gate.ok:
        warnings.append(gate.reason)
    return {
        "ok": not blockers,
        "reason": blockers[0] if blockers else "",
        "existing": False,
        "company": company,
        "title": title,
        "url": url,
        "location": location,
        "description": result.text,
        "staffing_firm": staffing_firm,
        "canonical_url": canonicalize_url(url),
        "blockers": blockers,
        "warnings": warnings,
        "fetch_via": "browser" if http_reason != "ok" else "http",
    }
