from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from openai import APITimeoutError, AsyncOpenAI, RateLimitError
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.config import get_settings
from app.db.enums import JobStatus, PackageStatus
from app.db.models import Job, JobPackage, as_utc
from app.services.folder_writer import ensure_job_folder, sync_job_folder
from app.services.pdf_export import export_pdf, markdown_to_html
from app.services.persona import get_active_persona_id, ground_truth_payload, load_persona
from app.services.resume_style import polish_flat_resume_html, replace_document_css

log = logging.getLogger("poong.tailor")


async def _upsert_package(
    session: AsyncSession,
    job: Job,
    *,
    persona_id: str,
    status: str,
    folder_path: str | None = None,
    notes: str | None = None,
) -> JobPackage:
    pkg = (
        await session.execute(
            select(JobPackage).where(
                JobPackage.job_id == job.id,
                JobPackage.persona_id == persona_id,
            )
        )
    ).scalar_one_or_none()
    if pkg is None:
        pkg = JobPackage(job_id=job.id, persona_id=persona_id)
        session.add(pkg)
    pkg.status = status
    if folder_path is not None:
        pkg.folder_path = folder_path
    pkg.notes = notes
    await session.flush()
    return pkg


async def _refresh_job_from_packages(session: AsyncSession, job: Job) -> None:
    """Keep listing lifecycle separate from per-persona package progress."""
    pkgs = (
        await session.execute(select(JobPackage).where(JobPackage.job_id == job.id))
    ).scalars().all()
    with_folder = [p for p in pkgs if p.folder_path]
    if with_folder:
        latest = max(
            with_folder,
            key=lambda p: as_utc(p.updated_at or p.created_at),
        )
        job.folder_path = latest.folder_path
    else:
        job.folder_path = None

    terminal_listing_states = {
        JobStatus.BLOCKED.value,
        JobStatus.SKIPPED.value,
        JobStatus.EXPIRED.value,
        JobStatus.REJECTED_ONSITE.value,
    }
    if job.status not in terminal_listing_states:
        job.status = (
            JobStatus.JD_READY.value
            if job.description and len(job.description) >= 50
            else JobStatus.NEW.value
        )
    # Listing notes are pipeline-level. Keep scan/manual notes; drop stale tailor errors.
    if (job.notes or "").startswith("tailor_failed:"):
        job.notes = None


def _client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=settings.openai_api_key, timeout=180.0, max_retries=0)


_RETRY_IN_RE = re.compile(r"try again in ([\d.]+)\s*s", re.I)


def _retry_wait_seconds(exc: BaseException, attempt: int) -> float:
    """Prefer OpenAI's suggested wait; otherwise exponential backoff."""
    msg = str(exc)
    m = _RETRY_IN_RE.search(msg)
    if m:
        return min(60.0, float(m.group(1)) + 0.75)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers:
        ra = headers.get("retry-after") or headers.get("Retry-After")
        if ra:
            try:
                return min(60.0, float(ra) + 0.5)
            except ValueError:
                pass
    return min(30.0, 1.5 * (2 ** (attempt - 1)))


async def _chat(system: str, user: str, *, attempts: int = 6) -> str:
    settings = get_settings()
    client = _client()
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.4,
            )
            return (resp.choices[0].message.content or "").strip()
        except (APITimeoutError, RateLimitError, TimeoutError) as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            wait = _retry_wait_seconds(exc, attempt)
            await asyncio.sleep(wait)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= attempts:
                break
            msg = str(exc).lower()
            if (
                "timeout" not in msg
                and "temporar" not in msg
                and "429" not in msg
                and "rate limit" not in msg
                and "connect" not in msg
            ):
                raise
            wait = _retry_wait_seconds(exc, attempt)
            await asyncio.sleep(wait)
    assert last_exc is not None
    raise last_exc


def _resume_user_message(persona, job: Job) -> str:
    gt = ground_truth_payload(persona, for_resume=True)
    jd = (job.description or "")[:20000]
    from app.services.resume_header import resume_contact_line, resume_location

    loc = resume_location(persona.identity)
    contact = resume_contact_line(persona.identity)
    return (
        "FIXED CANDIDATE GROUND TRUTH (immutable employers/titles/dates/education):\n"
        f"{json.dumps(gt, indent=2, ensure_ascii=False)}\n\n"
        "LOCKED RESUME HEADER — copy this contact block exactly. "
        "Only the professional title line may change to match the JD. "
        "Do not add street address, ZIP, citizenship, or work-authorization lines.\n"
        f"{persona.display_name}\n"
        f"{persona.default_professional_title}\n"
        f"{contact}\n"
        f"Resume location is {loc} only.\n\n"
        "TARGET JOB:\n"
        f"Company: {job.company}\n"
        f"Title: {job.title}\n"
        f"Location: {job.location or ''}\n"
        f"URL: {job.url}\n\n"
        "JOB DESCRIPTION:\n"
        f"{jd}\n\n"
        "Generate the complete tailored resume only."
    )


def _html_bundle_user_message(persona, job: Job) -> str:
    gt = ground_truth_payload(persona)
    jd = (job.description or "")[:20000]
    return (
        "Above is the job description. Below is the candidate profile. "
        "Tailor the resume to the job description.\n\n"
        f"JOB DESCRIPTION:\n{jd}\n\n"
        f"TARGET ROLE: {job.title} at {job.company}\n"
        f"Location: {job.location or ''}\n"
        f"URL: {job.url}\n\n"
        "CANDIDATE PROFILE (immutable employers/titles/dates/education/contact):\n"
        f"{json.dumps(gt, indent=2, ensure_ascii=False)}\n\n"
        "Follow the system instructions exactly. "
        "Output the complete HTML resume first, then the plain-text cover letter."
    )


def _cover_letter_system(persona) -> str:
    """Lock the signature to this persona, mirroring the resume header lock.

    The resume path enforces its header via enforce_resume_header(); without the
    equivalent here the model invents a signature from the persona's name
    parentheticals, so the resume and the cover letter disagree on who applied.
    """
    from app.services.resume_header import resume_location

    ident = persona.identity or {}
    location = resume_location(ident)
    email = str(ident.get("email") or "").strip()
    phone = str(ident.get("phone") or "").strip()
    signature = "\n".join(
        x for x in (persona.display_name, location, email, phone) if x
    )
    return (
        "You write truthful professional cover letters grounded only in the provided "
        "candidate ground truth and job description. Output plain text only, 300–450 words. "
        "Do not invent employers, metrics, or degrees. No markdown headings.\n\n"
        "LOCKED SIGNATURE — end the letter with exactly these lines after "
        "\"Sincerely,\". Copy them verbatim. Do not reformat the name, do not "
        "substitute any other spelling or variant of it, and do not add street "
        "address, ZIP, citizenship, or work-authorization lines:\n"
        f"{signature}"
    )


def _answers_system() -> str:
    return (
        "You answer ATS screening questions using only the provided persona screening_answers "
        "and identity. Return STRICT JSON object mapping question -> answer. "
        "If unknown, use a short honest default from identity (work auth, location, salary)."
    )


def _split_html_and_cover(raw: str) -> tuple[str, str]:
    text = raw.strip()
    if "cannot proceed: security clearance" in text.lower():
        raise RuntimeError("security_clearance_required")
    text = re.sub(r"^```(?:html)?\s*|\s*```$", "", text, flags=re.I | re.M).strip()
    lower = text.lower()
    start = lower.find("<!doctype html")
    if start < 0:
        start = lower.find("<html")
    if start < 0:
        raise RuntimeError("tailor_html_missing")
    end = lower.rfind("</html>")
    if end < 0:
        raise RuntimeError("tailor_html_incomplete")
    end += len("</html>")
    html = text[start:end].strip()
    cover = text[end:].strip()
    cover = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", cover, flags=re.I | re.M).strip()
    if cover.lower().startswith("cover letter"):
        cover = re.sub(r"(?i)^cover letter:?\s*", "", cover).strip()
    return html, cover


def _inject_css(html: str, css: str) -> str:
    html = re.sub(
        r'<link[^>]+rel=["\']stylesheet["\'][^>]*>\s*',
        "",
        html,
        flags=re.I,
    )
    style = f"<style>\n{css}\n</style>\n"
    if re.search(r"</head>", html, flags=re.I):
        return re.sub(r"</head>", style + "</head>", html, count=1, flags=re.I)
    if re.search(r"<body", html, flags=re.I):
        return re.sub(r"<body", style + "<body", html, count=1, flags=re.I)
    return style + html


def _load_persona_css(persona) -> str:
    css_name = (persona.tailor or {}).get("css_file")
    if not css_name:
        return ""
    base = get_settings().config_dir / "persona_identity"
    path = base / css_name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


async def generate_resume_markdown(persona, job: Job) -> str:
    from app.services.resume_header import enforce_resume_header

    raw = await _chat(persona.system_prompt, _resume_user_message(persona, job))
    return enforce_resume_header(raw, persona)


async def generate_html_bundle(persona, job: Job) -> tuple[str, str]:
    raw = await _chat(persona.system_prompt, _html_bundle_user_message(persona, job))
    return _split_html_and_cover(raw)


async def generate_cover_letter(persona, job: Job, resume_md: str) -> str:
    gt = ground_truth_payload(persona)
    user = (
        f"Candidate ground truth:\n{json.dumps(gt, indent=2, ensure_ascii=False)}\n\n"
        f"Target role: {job.title} at {job.company}\n"
        f"Location: {job.location or ''}\n"
        f"JD (excerpt):\n{(job.description or '')[:8000]}\n\n"
        f"Resume draft (for tone/alignment only):\n{resume_md[:6000]}\n\n"
        "Write the cover letter plain text only."
    )
    return await _chat(_cover_letter_system(persona), user)


async def generate_answers(persona, job: Job) -> dict[str, str]:
    questions = [a.get("question") for a in persona.screening_answers if a.get("question")]
    jd = job.description or ""
    for m in re.finditer(r"(?m)^\s*(?:\d+[.)]\s*)?([A-Z][^?\n]{8,120}\?)\s*$", jd):
        q = m.group(1).strip()
        if q not in questions:
            questions.append(q)
    user = (
        f"Persona screening_answers and identity:\n"
        f"{json.dumps({'screening_answers': persona.screening_answers, 'identity': persona.identity}, indent=2)}\n\n"
        f"Questions to answer:\n{json.dumps(questions, indent=2)}\n"
    )
    raw = await _chat(_answers_system(), user)
    try:
        raw2 = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
        data = json.loads(raw2)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except json.JSONDecodeError:
        pass
    return {a["question"]: a["answer"] for a in persona.screening_answers if "question" in a}


async def tailor_job(
    session: AsyncSession,
    job: Job,
    *,
    persona_id: str | None = None,
) -> dict[str, Any]:
    if not (job.description and len(job.description) >= 50):
        return {"job_id": job.id, "ok": False, "reason": "not_jd_ready"}

    # Generate for requested persona; do NOT overwrite listing job.persona_id
    pid = persona_id or get_active_persona_id() or job.persona_id
    persona = load_persona(pid)
    generate_pid = persona.persona_id
    folder = ensure_job_folder(generate_pid, job.seq_no or 0, job.company)
    display = persona.display_name
    mode = (persona.tailor or {}).get("mode") or "strategist_resume"

    try:
        if mode == "html_resume_bundle":
            resume_html, cover = await generate_html_bundle(persona, job)
            css = _load_persona_css(persona)
            if css:
                resume_html = _inject_css(resume_html, css)
            (folder / f"{display}.html").write_text(resume_html, encoding="utf-8")
            (folder / f"{display}.md").write_text(
                f"(HTML resume — see {display}.html)\n", encoding="utf-8"
            )
            await export_pdf(resume_html, folder / f"{display}.pdf")
            resume_for_answers = resume_html
        else:
            resume_md = await generate_resume_markdown(persona, job)
            (folder / f"{display}.md").write_text(resume_md, encoding="utf-8")
            resume_html = markdown_to_html(resume_md, title=f"{display} — Resume")
            css = _load_persona_css(persona)
            if css:
                resume_html = polish_flat_resume_html(resume_html, display_name=display)
                resume_html = replace_document_css(resume_html, css)
            (folder / f"{display}.html").write_text(resume_html, encoding="utf-8")
            await export_pdf(resume_html, folder / f"{display}.pdf")
            cover = await generate_cover_letter(persona, job, resume_md)
            resume_for_answers = resume_md

        answers = await generate_answers(persona, job)
        (folder / "answers.json").write_text(
            json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (folder / "questions.json").write_text(
            json.dumps(list(answers.keys()), indent=2, ensure_ascii=False), encoding="utf-8"
        )

        if not cover:
            cover = await generate_cover_letter(persona, job, resume_for_answers[:8000])
        (folder / "cover_letter.txt").write_text(cover, encoding="utf-8")
        (folder / f"{display} - Cover Letter.txt").write_text(cover, encoding="utf-8")
        cover_html = markdown_to_html(cover, title=f"{display} — Cover Letter")
        (folder / f"{display} - Cover Letter.html").write_text(cover_html, encoding="utf-8")
        await export_pdf(cover_html, folder / f"{display} - Cover Letter.pdf")

        sync_job_folder(
            persona_id=generate_pid,
            seq_no=job.seq_no or 0,
            company=job.company,
            title=job.title,
            url=job.url,
            location=job.location,
            status=PackageStatus.RESUME_READY.value,
            description=job.description,
            job_id=job.id,
            extra={"tailored": True, "persona_id": generate_pid},
        )
        pkg = await _upsert_package(
            session,
            job,
            persona_id=generate_pid,
            status=PackageStatus.RESUME_READY.value,
            folder_path=str(folder),
            notes=None,
        )
        try:
            await _refresh_job_from_packages(session, job)
        except Exception:  # noqa: BLE001
            log.exception(
                "listing refresh failed after successful tailor job_id=%s persona=%s",
                job.id,
                generate_pid,
            )
            # Package is already saved; listing aggregation must not fail the tailor.
            if job.description and len(job.description) >= 50:
                job.status = JobStatus.JD_READY.value
        log.info(
            "tailor ok job_id=%s seq=%s persona=%s folder=%s",
            job.id,
            job.seq_no,
            generate_pid,
            folder,
        )
        await session.flush()
        return {
            "job_id": job.id,
            "ok": True,
            "status": job.status,
            "folder": str(folder),
            "persona_id": generate_pid,
            "package": {
                "id": pkg.id,
                "persona_id": pkg.persona_id,
                "status": pkg.status,
                "folder_path": pkg.folder_path,
            },
        }
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)[:400]
        log.exception(
            "tailor failed job_id=%s seq=%s persona=%s: %s",
            job.id,
            job.seq_no,
            generate_pid,
            reason,
        )
        pkg = await _upsert_package(
            session,
            job,
            persona_id=generate_pid,
            status=PackageStatus.FAILED.value,
            notes=f"tailor_failed:{reason}",
        )
        try:
            await _refresh_job_from_packages(session, job)
        except Exception:  # noqa: BLE001
            if job.description and len(job.description) >= 50:
                job.status = JobStatus.JD_READY.value
        job.notes = f"tailor_failed:{reason}"
        await session.flush()
        return {
            "job_id": job.id,
            "ok": False,
            "reason": reason,
            "persona_id": generate_pid,
            "package": {
                "id": pkg.id,
                "persona_id": pkg.persona_id,
                "status": pkg.status,
                "folder_path": pkg.folder_path,
                "notes": pkg.notes,
            },
        }
