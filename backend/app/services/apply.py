from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.enums import (
    ApplicationOutcome,
    ApplySessionStatus,
    JobStatus,
    PackageStatus,
)
from app.db.models import Application, Job, JobPackage, utcnow
from app.services.apply_nav import detect_hard_block, promote_application
from app.services.apply_session import ApplySession, apply_sessions
from app.services.browser_attach import BrowserAttach
from app.services.folder_writer import sync_job_folder
from app.services.form_scan import scan_form_fields, write_form_fields
from app.services.human_input import human_click, human_pause, soft_cloudflare_wait
from app.services.persona import load_persona
from app.services.submission_capture import (
    POST_SUBMIT,
    PRE_SUBMIT,
    capture_submission,
)
from app.validators.dedupe import normalize_company


async def _resolve_package(
    session: AsyncSession,
    job: Job,
    *,
    persona_id: str | None = None,
    package_id: int | None = None,
) -> JobPackage:
    if package_id is not None:
        pkg = await session.get(JobPackage, package_id)
        if not pkg or pkg.job_id != job.id:
            raise ValueError("package_not_found")
        return pkg

    pkgs = (
        await session.execute(select(JobPackage).where(JobPackage.job_id == job.id))
    ).scalars().all()
    ready = [
        p
        for p in pkgs
        if p.status
        in (
            PackageStatus.RESUME_READY.value,
            PackageStatus.APPLYING.value,
        )
        and p.folder_path
    ]
    if persona_id:
        for p in ready:
            if p.persona_id == persona_id:
                return p
        raise ValueError(f"no_package_for_persona:{persona_id}")
    if len(ready) == 1:
        return ready[0]
    if len(ready) == 0:
        raise ValueError("no_resume_package")
    raise ValueError("multiple_packages_pick_persona")


async def start_apply(
    session: AsyncSession,
    job: Job,
    *,
    ui_opened_tab: bool = True,
    persona_id: str | None = None,
    package_id: int | None = None,
) -> ApplySession:
    if job.status in {
        JobStatus.BLOCKED.value,
        JobStatus.SKIPPED.value,
        JobStatus.EXPIRED.value,
        JobStatus.REJECTED_ONSITE.value,
    }:
        raise ValueError(f"listing_not_applyable:{job.status}")

    pkg = await _resolve_package(
        session, job, persona_id=persona_id, package_id=package_id
    )
    apply_url = job.apply_url or job.url
    pkg.status = PackageStatus.APPLYING.value
    await session.commit()

    target_persona = pkg.persona_id
    target_folder = pkg.folder_path
    target_package_id = pkg.id

    async def runner(apply_sess: ApplySession) -> None:
        await _run_apply(
            apply_sess,
            persona_id=target_persona,
            folder_path=target_folder,
            package_id=target_package_id,
        )

    return await apply_sessions.start(
        job.id,
        apply_url=apply_url,
        ui_opened_tab=ui_opened_tab,
        runner=runner,
    )


async def _run_apply(
    apply_sess: ApplySession,
    *,
    persona_id: str,
    folder_path: str | None,
    package_id: int | None = None,
) -> None:
    attach = BrowserAttach()
    apply_sess.attach = attach
    apply_sess.status = ApplySessionStatus.STARTING
    apply_sess.log(f"Apply runner start job_id={apply_sess.job_id} url={apply_sess.apply_url}")
    apply_sess.log(f"ui_opened_tab={apply_sess.ui_opened_tab}")
    apply_sess.log(f"package persona={persona_id} folder={folder_path}")

    def emit(msg: str, level: str = "info") -> None:
        apply_sess.log(msg, level)

    from app.services.apply_answers import dedupe_fields, generate_field_answers
    from app.services.apply_fill import fill_all_answers, find_package_documents
    from app.services.form_scan import scan_required_empty, write_answers

    result = await attach.attach(
        target_url=apply_sess.apply_url,
        ui_opened_tab=apply_sess.ui_opened_tab,
        emit=emit,
    )
    if not result.ok:
        apply_sess.status = ApplySessionStatus.FAILED
        apply_sess.error = result.reason
        apply_sess.log(f"Attach failed: {result.reason}", "error")
        await _revert_applying(apply_sess.job_id, package_id=package_id)
        return

    apply_sess.log(
        f"Attach ok reason={result.reason} created_new_page={result.created_new_page}"
    )
    page = result.page
    apply_sess.log(f"Using page URL: {page.url}")
    apply_sess.status = ApplySessionStatus.FILLING

    hard = await detect_hard_block(page)
    if hard:
        apply_sess.status = ApplySessionStatus.FAILED
        apply_sess.error = hard
        apply_sess.log(f"Hard block: {hard}", "error")
        await _revert_applying(apply_sess.job_id, package_id=package_id)
        return

    if await soft_cloudflare_wait(page):
        apply_sess.status = ApplySessionStatus.AWAITING_HUMAN
        apply_sess.log("Soft Cloudflare challenge — waiting for human Resume")
        apply_sess.resume_event.clear()
        await apply_sess.resume_event.wait()
        if apply_sess.cancel_event.is_set():
            apply_sess.log("Cancelled while awaiting human")
            return
        apply_sess.status = ApplySessionStatus.FILLING
        apply_sess.log("Resumed after human")

    apply_sess.log("Looking for Apply CTA / form (multi-ATS)")
    family = await promote_application(page, emit=lambda m: apply_sess.log(m))
    await human_pause(0.35, 0.9)
    apply_sess.log(f"After nav promote ({family}), URL={page.url}")

    fields = await scan_form_fields(page)
    apply_sess.log(f"Scanned {len(fields)} form fields")
    for f in fields[:25]:
        req = " *" if f.get("required") else ""
        apply_sess.log(
            f"  field{req}: {(f.get('label') or f.get('name') or '?')[:70]} "
            f"({f.get('kind')}/{f.get('type')}) opts={len(f.get('options') or [])}"
        )

    answers: dict = {}
    folder: Path | None = Path(folder_path) if folder_path else None
    resume_pdf = None
    cover_pdf = None

    try:
        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as db:
            job = await db.get(Job, apply_sess.job_id)
            if not job:
                apply_sess.status = ApplySessionStatus.FAILED
                apply_sess.error = "job_missing"
                apply_sess.log("Job row missing in DB", "error")
                return
            persona = load_persona(persona_id)
            apply_sess.log(f"Persona loaded: {persona.persona_id} / {persona.display_name}")
            if folder:
                write_form_fields(folder, fields)
                resume_pdf, cover_pdf = find_package_documents(folder)
                apply_sess.log(
                    f"Documents: resume={resume_pdf.name if resume_pdf else None} "
                    f"cover={cover_pdf.name if cover_pdf else None}"
                )

            resume_excerpt = ""
            if folder:
                for p in sorted(folder.glob("*.md")):
                    if "cover" in p.name.lower():
                        continue
                    resume_excerpt = p.read_text(encoding="utf-8")[:6000]
                    break
                if not resume_excerpt:
                    for p in sorted(folder.glob("*.html")):
                        if "cover" in p.name.lower():
                            continue
                        resume_excerpt = p.read_text(encoding="utf-8")[:6000]
                        break

            apply_sess.log("Generating answers (static defaults + GPT for remaining fields)…")
            fields = dedupe_fields(fields)
            apply_sess.log(f"After dedupe: {len(fields)} form fields")
            answers = await generate_field_answers(
                persona=persona,
                fields=fields,
                job_meta={
                    "company": job.company,
                    "title": job.title,
                    "location": job.location,
                    "url": job.url,
                },
                resume_excerpt=resume_excerpt,
                jd_excerpt=(job.description or "")[:8000],
            )
            apply_sess.log(f"Answer map ready: {len(answers)} fields")
            for k, v in list(answers.items())[:15]:
                apply_sess.log(f"  answer: {k[:50]} -> {str(v)[:50]}")

            if folder:
                write_answers(folder, answers)

            apply_sess.log("Filling form (human typing + dropdowns + resume upload)…")
            stats = await fill_all_answers(
                page,
                fields,
                answers,
                emit=lambda m, lvl="info": apply_sess.log(m, lvl),
                resume_pdf=resume_pdf,
                cover_pdf=cover_pdf,
                skip_filled=True,
            )
            apply_sess.log(
                f"Fill pass complete: filled={stats['filled']} "
                f"skipped={stats.get('skipped', 0)} failed={len(stats['failed'])}"
            )
            if stats["failed"]:
                apply_sess.log(
                    "Unfilled: " + "; ".join(stats["failed"][:12]),
                    "error",
                )

            # Probe submit to surface red validation errors, then re-fill ONLY failures
            apply_sess.log("Probe-submit to surface validation errors…")
            await _probe_submit(page, apply_sess)
            await human_pause(0.4, 0.9)
            missing = await scan_required_empty(page)
            if missing:
                apply_sess.log(
                    "Validation after probe: " + "; ".join(missing[:15]),
                    "error",
                )
            failed_labels = set(stats.get("failed") or [])
            for attempt in range(1, 4):
                if page.is_closed():
                    apply_sess.log("Page closed — stopping fix loop", "error")
                    break
                fields_n = dedupe_fields(await scan_form_fields(page))
                missing = await scan_required_empty(page)
                if missing:
                    apply_sess.log(
                        f"Validation signals: " + "; ".join(missing[:12]),
                        "error",
                    )
                if not missing and not failed_labels:
                    # Still check empty required text/tel on page
                    empty_req = [
                        (f.get("label") or "")
                        for f in fields_n
                        if f.get("required")
                        and (f.get("kind") in {"text", "tel", None} or (f.get("type") or "") in {"text", "tel", "email"})
                        and not (f.get("value") or "").strip()
                        and (f.get("label") or "")
                    ]
                    if not empty_req:
                        break
                    missing = empty_req
                miss_l = {m.lower() for m in missing}
                only: set[str] = set(failed_labels)
                for f in fields_n:
                    lab = (f.get("label") or "")
                    lab_l = lab.lower().strip()
                    if not lab_l:
                        continue
                    # Match validation blurb to field label (Ashby: "Phone Number")
                    if any(
                        m in lab_l
                        or lab_l in m
                        or any(tok and tok in m for tok in lab_l.split() if len(tok) > 3)
                        for m in miss_l
                        if m
                    ):
                        only.add(lab)
                    if f.get("required") and not (f.get("value") or "").strip():
                        kind = (f.get("kind") or f.get("type") or "").lower()
                        if kind in {"text", "tel", "email", "select", "combobox", ""}:
                            only.add(lab)
                if resume_pdf:
                    for f in fields_n:
                        if (f.get("kind") or f.get("type")) == "file":
                            # Always re-target resume/file fields so package CV replaces defaults
                            only.add(f.get("label") or "Resume")
                # If validation mentions a field name directly, add it even if scan missed the control
                for m in missing:
                    ml = m.lower()
                    for f in fields_n:
                        lab = (f.get("label") or "").strip()
                        if lab and lab.lower() in ml:
                            only.add(lab)
                if not only:
                    apply_sess.log(f"Fix pass {attempt}: nothing left to target")
                    break
                apply_sess.log(f"Fix pass {attempt}: targeting {len(only)} fields — {list(only)[:8]}")
                retry_fields = [
                    f
                    for f in fields_n
                    if (f.get("label") or "") in only
                    or any(
                        (f.get("label") or "").lower() in o.lower()
                        or o.lower() in (f.get("label") or "").lower()
                        for o in only
                    )
                ]
                if retry_fields:
                    extra = await generate_field_answers(
                        persona=persona,
                        fields=retry_fields,
                        job_meta={
                            "company": job.company,
                            "title": job.title,
                            "location": job.location,
                            "url": job.url,
                        },
                        resume_excerpt=resume_excerpt,
                        jd_excerpt=(job.description or "")[:8000],
                    )
                    answers.update(extra)
                stats = await fill_all_answers(
                    page,
                    fields_n if fields_n else retry_fields,
                    answers,
                    emit=lambda m, lvl="info": apply_sess.log(m, lvl),
                    resume_pdf=resume_pdf,
                    cover_pdf=cover_pdf,
                    only_labels=only,
                    skip_filled=True,
                )
                failed_labels = set(stats.get("failed") or [])
                await _probe_submit(page, apply_sess)
                await human_pause(0.35, 0.8)

            missing = await scan_required_empty(page)
            if missing:
                apply_sess.log(
                    "Still showing required errors: " + "; ".join(missing[:15]),
                    "error",
                )
                apply_sess.status = ApplySessionStatus.AWAITING_CONFIRM
                apply_sess.log(
                    "Partial fill — review manually, then Confirm submit or Mark applied"
                )
            else:
                apply_sess.log("No visible validation errors after fill/fix loop")
    except Exception as exc:  # noqa: BLE001
        apply_sess.log(f"Fill/answer pipeline error: {exc}", "error")
        apply_sess.status = ApplySessionStatus.NEEDS_MANUAL
        return

    # Evidence of what the form actually contained before anything was sent.
    await capture_submission(page, folder, PRE_SUBMIT, emit=emit)

    settings = get_settings()
    if settings.auto_submit:
        apply_sess.status = ApplySessionStatus.SUBMITTING
        apply_sess.log("AUTO_SUBMIT enabled — attempting submit")
        ok = await _click_submit(page, apply_sess)
        await capture_submission(page, folder, POST_SUBMIT, emit=emit)
        apply_sess.status = (
            ApplySessionStatus.APPLIED if ok else ApplySessionStatus.NEEDS_MANUAL
        )
        return

    apply_sess.status = ApplySessionStatus.AWAITING_CONFIRM
    apply_sess.log("Awaiting human Confirm submit / Mark applied (does NOT auto-mark DB applied)")
    while not apply_sess.cancel_event.is_set():
        if apply_sess.confirm_event.is_set():
            apply_sess.confirm_event.clear()
            apply_sess.status = ApplySessionStatus.SUBMITTING
            apply_sess.log("Confirm received — click submit + fix any new validation errors")
            ok = await _click_submit(page, apply_sess)
            if ok:
                missing = await scan_required_empty(page)
                if missing and answers:
                    apply_sess.log("Post-submit errors — attempting one more fill pass")
                    fields_n = await scan_form_fields(page)
                    await fill_all_answers(
                        page,
                        fields_n,
                        answers,
                        emit=lambda m, lvl="info": apply_sess.log(m, lvl),
                        resume_pdf=resume_pdf,
                        cover_pdf=cover_pdf,
                    )
                    await _click_submit(page, apply_sess)
                # What the employer's page said back after submitting.
                await capture_submission(page, folder, POST_SUBMIT, emit=emit)
                apply_sess.log(
                    "Submit click done — verify on the page. "
                    "Use Mark applied only after you confirm it actually submitted."
                )
                apply_sess.status = ApplySessionStatus.AWAITING_CONFIRM
            else:
                apply_sess.status = ApplySessionStatus.NEEDS_MANUAL
            continue
        await asyncio.sleep(0.4)


async def _find_submit_button(page):
    for part in (
        "button#resumator-submit-btn",
        "input#resumator-submit-btn",
        "#submit_app",
        "input[type='submit']",
        "button:has-text('Submit Application')",
        "button:has-text('Submit application')",
        "button:has-text('Submit')",
        "button:has-text('Apply Now')",
        "button:has-text('Send Application')",
        ".submit-application",
        "a.button:has-text('Submit')",
    ):
        loc = page.locator(part).first
        try:
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:  # noqa: BLE001
            continue
    return None


async def _probe_submit(page, apply_sess: ApplySession) -> bool:
    """Click Submit to surface red validation errors (do not treat as success)."""
    try:
        if page.is_closed():
            apply_sess.log("Probe submit: page closed", "error")
            return False
        btn = await _find_submit_button(page)
        if not btn:
            apply_sess.log("Probe submit: button not found")
            return False
        disabled = await btn.is_disabled()
        if disabled:
            apply_sess.log("Probe submit: button disabled — will still try click")
        await human_click(page, btn, timeout=4000)
        apply_sess.log("Probe submit clicked (to reveal validation errors)")
        await human_pause(0.5, 1.0)
        return True
    except Exception as exc:  # noqa: BLE001
        apply_sess.log(f"Probe submit failed: {exc}", "error")
        return False


async def _click_submit(page, apply_sess: ApplySession) -> bool:
    from app.services.form_scan import scan_required_empty

    try:
        if page.is_closed():
            apply_sess.log("Submit: page closed", "error")
            return False
        btn = await _find_submit_button(page)
        if not btn:
            apply_sess.log("Submit button not found", "error")
            return False
        disabled = await btn.is_disabled()
        if disabled:
            apply_sess.log("Submit button disabled — attempting click anyway to surface errors")
        await human_click(page, btn, timeout=5000)
        apply_sess.log("Submit button clicked")
        await human_pause(0.8, 1.4)
        missing2 = await scan_required_empty(page)
        if missing2:
            apply_sess.log(
                "After click, validation errors remain: " + "; ".join(missing2[:12]),
                "error",
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        apply_sess.log(f"Submit click failed: {exc}", "error")
        return False


async def _revert_applying(job_id: int, *, package_id: int | None = None) -> None:
    """If attach fails, don't leave the target package stuck in applying."""
    try:
        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as db:
            job = await db.get(Job, job_id)
            if package_id is not None:
                pkg = await db.get(JobPackage, package_id)
                if pkg and pkg.status == PackageStatus.APPLYING.value:
                    pkg.status = PackageStatus.RESUME_READY.value
            await db.commit()
    except Exception:  # noqa: BLE001
        pass


async def mark_job_applied(
    session: AsyncSession,
    job: Job,
    *,
    persona_id: str | None = None,
    package_id: int | None = None,
) -> Job:
    pkgs = (
        await session.execute(select(JobPackage).where(JobPackage.job_id == job.id))
    ).scalars().all()
    pkg: JobPackage | None = None
    if package_id is not None:
        pkg = next((p for p in pkgs if p.id == package_id), None)
        if not pkg:
            raise ValueError("package_not_found")
        if persona_id and pkg.persona_id != persona_id:
            raise ValueError("package_persona_mismatch")
    elif persona_id:
        pkg = next((p for p in pkgs if p.persona_id == persona_id), None)
        if not pkg:
            raise ValueError(f"no_package_for_persona:{persona_id}")
    elif len(pkgs) == 1:
        pkg = pkgs[0]
    elif not pkgs:
        raise ValueError("no_resume_package")
    else:
        raise ValueError("multiple_packages_pick_persona")

    apply_persona = pkg.persona_id
    pkg.status = PackageStatus.APPLIED.value
    if pkg.applied_at is None:
        pkg.applied_at = utcnow()
    if not pkg.outcome:
        pkg.outcome = ApplicationOutcome.PENDING.value

    # Listing is a shared JD pipeline. Applying is recorded only on the package.
    if job.status not in {
        JobStatus.BLOCKED.value,
        JobStatus.SKIPPED.value,
        JobStatus.EXPIRED.value,
        JobStatus.REJECTED_ONSITE.value,
        # Applying with a second persona must not walk the listing back from
        # an interview already reached with the first.
        JobStatus.INTERVIEW.value,
    }:
        job.status = (
            JobStatus.JD_READY.value
            if job.description and len(job.description) >= 50
            else JobStatus.NEW.value
        )

    normalized = job.normalized_company or normalize_company(job.company)

    existing = await session.execute(
        select(Application).where(
            Application.persona_id == apply_persona,
            Application.normalized_company == normalized,
        )
    )
    app = existing.scalar_one_or_none()
    if app:
        app.status = "applied"
        app.job_id = job.id
    else:
        session.add(
            Application(
                persona_id=apply_persona,
                company=job.company,
                normalized_company=normalized,
                job_id=job.id,
                status="applied",
            )
        )

    folder_persona = pkg.persona_id
    if job.seq_no:
        sync_job_folder(
            persona_id=folder_persona,
            seq_no=job.seq_no,
            company=job.company,
            title=job.title,
            url=job.url,
            location=job.location,
            status=pkg.status,
            description=job.description,
            job_id=job.id,
        )
    await session.commit()

    # Append to Google Sheet tracker before ending session (so UI log can show it)
    sheet_msg = ""
    try:
        from app.services.applied_sheet import log_applied_to_sheet

        sheet_result = await log_applied_to_sheet(
            title=job.title,
            company=job.company,
            url=job.apply_url or job.url,
            persona_id=apply_persona,
            job_id=job.id,
            location=job.location,
        )
        if sheet_result.get("sheet"):
            sheet_msg = "Logged to Google Sheet (title / company / link)"
        elif sheet_result.get("errors"):
            sheet_msg = (
                "Applied saved locally; Sheet not linked yet — "
                + "; ".join(sheet_result["errors"][:2])
            )
        else:
            sheet_msg = "Applied mirrored to data/applied_sheet_log.csv"
    except Exception as exc:  # noqa: BLE001
        sheet_msg = f"Sheet log failed: {exc}"

    sess = apply_sessions.get(job.id)
    if sess and sheet_msg:
        sess.log(sheet_msg, "info" if "Logged to Google" in sheet_msg else "error")

    await apply_sessions.end_session(job.id, status=ApplySessionStatus.APPLIED)
    return job


async def stop_apply_for_job(
    session: AsyncSession,
    job: Job,
    *,
    revert_packages: bool = True,
) -> None:
    """End in-memory apply session and optionally revert package applying → resume_ready."""
    if revert_packages:
        pkgs = (
            await session.execute(select(JobPackage).where(JobPackage.job_id == job.id))
        ).scalars().all()
        for p in pkgs:
            if p.status == PackageStatus.APPLYING.value:
                p.status = PackageStatus.RESUME_READY.value
    await apply_sessions.end_session(job.id, status=ApplySessionStatus.CANCELLED)
