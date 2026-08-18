from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.enums import JobStatus
from app.db.models import Job, JobPackage
from app.db.session import get_db
from app.services.apply_answers import generate_field_answers
from app.services.folder_writer import ensure_job_folder
from app.services.jd_extract import extract_job_fields_from_jd
from app.services.persona import get_active_persona_id, load_persona
from app.services.pipeline import find_job_by_url, process_listing
from app.services.deep_scan import deep_scan_job, preview_url_fetch
from app.services.tailor import tailor_job

router = APIRouter(prefix="/api/manual-apply", tags=["manual-apply"])


class ManualPrepareBody(BaseModel):
    persona_id: str
    description: str = Field(..., min_length=50)
    url: str | None = None
    company: str | None = None
    title: str | None = None
    location: str | None = None


class ManualFetchBody(BaseModel):
    persona_id: str
    url: str | None = None
    job_id: int | None = None


class ManualTailorBody(BaseModel):
    job_id: int
    persona_id: str


class ManualAnswersBody(BaseModel):
    persona_id: str
    job_id: int | None = None
    questions: list[str] | None = None
    questions_text: str | None = None
    company: str | None = None
    title: str | None = None
    url: str | None = None
    description: str | None = None


def _parse_questions(*, questions: list[str] | None, questions_text: str | None) -> list[str]:
    out: list[str] = []
    if questions:
        out.extend(q.strip() for q in questions if q and str(q).strip())
    if questions_text:
        for raw in questions_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            line = re.sub(r"^\s*(?:\d+[.)\]:-]|\*|-)\s*", "", line).strip()
            if line:
                out.append(line)
    seen: set[str] = set()
    unique: list[str] = []
    for q in out:
        key = q.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(q)
    return unique


def _resume_excerpt_from_folder(folder_path: str | None) -> str:
    if not folder_path:
        return ""
    folder = Path(folder_path)
    if not folder.exists():
        return ""
    for pattern in ("*.md", "*.html", "*.txt"):
        for path in sorted(folder.glob(pattern)):
            name = path.name.lower()
            if "cover" in name or "answer" in name or "question" in name:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                continue
            if text:
                return text[:8000]
    return ""


def _fallback_url(company: str, title: str, jd: str) -> str:
    digest = hashlib.sha1(f"{company}|{title}|{jd[:400]}".encode("utf-8")).hexdigest()[:12]
    return f"https://manual.poong.local/jobs/{digest}"


async def _package_for_persona(
    db: AsyncSession, job_id: int, persona_id: str
) -> JobPackage | None:
    return (
        await db.execute(
            select(JobPackage).where(
                JobPackage.job_id == job_id,
                JobPackage.persona_id == persona_id,
            )
        )
    ).scalar_one_or_none()


def _job_payload(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "seq_no": job.seq_no,
        "company": job.company,
        "title": job.title,
        "url": job.url,
        "apply_url": job.apply_url,
        "location": job.location,
        "status": job.status,
        "description": job.description,
        "folder_path": job.folder_path,
        "packages": [
            {
                "id": p.id,
                "persona_id": p.persona_id,
                "status": p.status,
                "folder_path": p.folder_path,
            }
            for p in (job.packages or [])
        ],
    }


@router.post("/fetch")
async def manual_fetch(body: ManualFetchBody, db: AsyncSession = Depends(get_db)):
    try:
        load_persona(body.persona_id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    job = None
    if body.job_id is not None:
        job = (
            await db.execute(
                select(Job).options(selectinload(Job.packages)).where(Job.id == body.job_id)
            )
        ).scalar_one_or_none()
        if not job:
            raise HTTPException(404, detail="not_found")
        url = (body.url or job.apply_url or job.url or "").strip()
        preview = await preview_url_fetch(
            db, url=url, persona_id=body.persona_id, job=job
        )
        await db.commit()
        job = (
            await db.execute(
                select(Job).options(selectinload(Job.packages)).where(Job.id == job.id)
            )
        ).scalar_one()
        preview["job"] = _job_payload(job)
        return preview

    url = (body.url or "").strip()
    preview = await preview_url_fetch(db, url=url, persona_id=body.persona_id)
    await db.commit()
    if preview.get("job_id"):
        job = (
            await db.execute(
                select(Job).options(selectinload(Job.packages)).where(Job.id == preview["job_id"])
            )
        ).scalar_one_or_none()
        if job:
            preview["job"] = _job_payload(job)
    return preview


@router.post("/prepare")
async def manual_prepare(body: ManualPrepareBody, db: AsyncSession = Depends(get_db)):
    try:
        load_persona(body.persona_id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    jd = body.description.strip()
    extracted = await extract_job_fields_from_jd(jd)

    company = (body.company or "").strip() or extracted["company"]
    title = (body.title or "").strip() or extracted["title"]
    url = (body.url or "").strip() or extracted["url"]
    location = (body.location or "").strip() or extracted["location"] or "Remote, US"
    url_inferred = False
    if not url:
        url = _fallback_url(company, title, jd)
        url_inferred = True

    listing = {
        "company": company,
        "title": title,
        "url": url,
        "location": location,
        "description": jd,
        "source": "manual_apply",
    }
    existing = await find_job_by_url(db, url)
    if existing and not url_inferred:
        job = (
            await db.execute(
                select(Job).options(selectinload(Job.packages)).where(Job.id == existing.id)
            )
        ).scalar_one()
        if jd and len(jd) >= 50 and (not job.description or len(job.description) < 50):
            job.description = jd
            job.status = JobStatus.JD_READY.value
            await db.commit()
            job = (
                await db.execute(
                    select(Job).options(selectinload(Job.packages)).where(Job.id == job.id)
                )
            ).scalar_one()
        return {
            "ok": True,
            "job": _job_payload(job),
            "persona_id": body.persona_id,
            "extracted": extracted,
            "url_inferred": url_inferred,
            "warning": "duplicate_url",
            "resume_save_hint": (
                f"data/applications/{body.persona_id}/{{seq}}-{company.replace(' ', '-')}/"
            ),
        }

    result = await process_listing(
        db,
        listing,
        persona_id=body.persona_id or get_active_persona_id(),
        skip_content_gates=True,
        bypass_gates=False,
    )
    await db.commit()
    if result.reason == "duplicate_url" and result.job:
        job = (
            await db.execute(
                select(Job).options(selectinload(Job.packages)).where(Job.id == result.job.id)
            )
        ).scalar_one()
        return {
            "ok": True,
            "job": _job_payload(job),
            "persona_id": body.persona_id,
            "extracted": extracted,
            "url_inferred": url_inferred,
            "warning": "duplicate_url",
            "resume_save_hint": (
                f"data/applications/{body.persona_id}/{{seq}}-{company.replace(' ', '-')}/"
            ),
        }
    if result.reason in {"company_already_applied", "company_blocked"}:
        raise HTTPException(409, detail=result.reason)
    if not result.job:
        raise HTTPException(400, detail=result.reason or "insert_failed")

    job = (
        await db.execute(
            select(Job).options(selectinload(Job.packages)).where(Job.id == result.job.id)
        )
    ).scalar_one()
    # Manual JD paste should always be ready for generate/apply
    if job.description and len(job.description) >= 50:
        job.status = JobStatus.JD_READY.value
        if (job.notes or "").startswith("upload_gate:"):
            job.notes = None
        await db.commit()
        # refresh() expires relationships; reload with selectinload for async SQLite
        job = (
            await db.execute(
                select(Job).options(selectinload(Job.packages)).where(Job.id == job.id)
            )
        ).scalar_one()

    return {
        "ok": True,
        "job": _job_payload(job),
        "persona_id": body.persona_id,
        "extracted": extracted,
        "url_inferred": url_inferred,
        "resume_save_hint": (
            f"data/applications/{body.persona_id}/{{seq}}-{company.replace(' ', '-')}/"
        ),
    }


@router.post("/tailor")
async def manual_tailor(body: ManualTailorBody, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, body.job_id)
    if not job:
        raise HTTPException(404, detail="not_found")
    try:
        load_persona(body.persona_id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    if not (job.description and len(job.description) >= 50):
        raise HTTPException(400, detail="jd_too_short")
    try:
        result = await tailor_job(db, job, persona_id=body.persona_id)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=str(exc)) from exc

    job = (
        await db.execute(
            select(Job).options(selectinload(Job.packages)).where(Job.id == body.job_id)
        )
    ).scalar_one()
    pkg = await _package_for_persona(db, body.job_id, body.persona_id)
    return {
        **result,
        "job": _job_payload(job),
        "resume_folder": (pkg.folder_path if pkg else result.get("folder")),
        "resume_files": {
            "pdf": f"{(pkg.folder_path if pkg else result.get('folder'))}",
            "note": "PDF/HTML/MD live in this folder for Assisted apply upload",
        },
    }


@router.post("/answers")
async def manual_answers(body: ManualAnswersBody, db: AsyncSession = Depends(get_db)):
    try:
        persona = load_persona(body.persona_id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    questions = _parse_questions(questions=body.questions, questions_text=body.questions_text)
    if not questions:
        raise HTTPException(400, detail="no_questions")

    job: Job | None = None
    resume_excerpt = ""
    jd_excerpt = (body.description or "").strip()
    company = body.company
    title = body.title
    url = body.url

    if body.job_id is not None:
        job = await db.get(Job, body.job_id)
        if not job:
            raise HTTPException(404, detail="job_not_found")
        company = company or job.company
        title = title or job.title
        url = url or job.url
        jd_excerpt = jd_excerpt or (job.description or "")
        pkg = await _package_for_persona(db, job.id, body.persona_id)
        if pkg and pkg.folder_path:
            resume_excerpt = _resume_excerpt_from_folder(pkg.folder_path)
        if not resume_excerpt and job.folder_path:
            resume_excerpt = _resume_excerpt_from_folder(job.folder_path)

    if not jd_excerpt or len(jd_excerpt) < 40:
        raise HTTPException(400, detail="jd_required")

    fields: list[dict[str, Any]] = [
        {"label": q, "kind": "text", "type": "text", "required": True, "options": []}
        for q in questions
    ]
    answers = await generate_field_answers(
        persona=persona,
        fields=fields,
        job_meta={
            "company": company,
            "title": title,
            "location": job.location if job else None,
            "url": url,
        },
        resume_excerpt=resume_excerpt,
        jd_excerpt=jd_excerpt,
    )

    ordered = [{"question": q, "answer": answers.get(q, "")} for q in questions]
    answer_map = {row["question"]: row["answer"] for row in ordered}

    saved_folder: str | None = None
    if job is not None:
        pkg = await _package_for_persona(db, job.id, body.persona_id)
        if pkg and pkg.folder_path:
            folder = Path(pkg.folder_path)
            folder.mkdir(parents=True, exist_ok=True)
        else:
            folder = ensure_job_folder(
                body.persona_id, int(job.seq_no or 0), job.company or "Company"
            )
            if pkg:
                pkg.folder_path = str(folder)
                await db.commit()
        (folder / "questions.json").write_text(
            json.dumps(questions, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (folder / "answers.json").write_text(
            json.dumps(answer_map, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        qa_text = "\n\n".join(
            f"Q: {row['question']}\nA: {row['answer'] or ''}" for row in ordered
        )
        (folder / "application_qa.txt").write_text(qa_text, encoding="utf-8")
        saved_folder = str(folder)

    return {
        "ok": True,
        "persona_id": body.persona_id,
        "job_id": body.job_id,
        "resume_used": bool(resume_excerpt),
        "answers": ordered,
        "answer_map": answer_map,
        "saved_folder": saved_folder,
    }
