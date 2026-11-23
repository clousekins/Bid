from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.enums import JobStatus
from app.db.models import JobRow
from app.services.folder_writer import ensure_job_folder, sync_job_folder
from app.services.pdf_export import export_pdf, markdown_to_html
from app.services.persona import ground_truth_payload, load_persona
from app.services import runtime_settings
from app.services.ats_score import extract_terms, persona_corpus, score_resume
from app.services.resume_credibility import score_credibility
from app.services.resume_style import polish_flat_resume_html, replace_document_css
from app.services.openai_compat import BackgroundResponseFailed, background_response_text

log = logging.getLogger("poong.tailor")


# Statuses a (re-)tailor must not walk back. Regenerating a resume for a job
# already applied, at interview, or set aside by hand must not reset it.
_NO_REGEN_STATUS = {
    JobStatus.BLOCKED.value,
    JobStatus.SKIPPED.value,
    JobStatus.EXPIRED.value,
    JobStatus.REJECTED_ONSITE.value,
    JobStatus.INTERVIEW.value,
    JobStatus.APPLIED.value,
    JobStatus.APPLYING.value,
}


def _set_row_status(job: JobRow, status: str) -> None:
    """Move the row's own status, unless it is somewhere a tailor must not undo."""
    if job.status not in _NO_REGEN_STATUS:
        job.status = status


def _client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=settings.openai_api_key, timeout=180.0, max_retries=0)


# Chat-capable ids start with gpt/o<n>/chatgpt; the substrings below are the
# non-chat families (audio, images, embeddings, …) to drop from the picker.
_CHAT_MODEL_RE = re.compile(r"^(gpt|o\d|chatgpt)", re.I)
_NON_CHAT = (
    "embedding", "whisper", "tts", "audio", "realtime", "image", "dall-e",
    "moderation", "transcribe", "search", "codex", "computer-use",
)


async def list_models() -> list[str]:
    """Chat-capable model ids from the OpenAI account, newest first. Best-effort:
    returns [] when there is no key or the call fails, so the picker can fall
    back to the configured model."""
    try:
        client = _client()
    except RuntimeError:
        return []
    try:
        resp = await client.models.list()
    except Exception:  # noqa: BLE001
        return []
    ids = [
        m.id
        for m in getattr(resp, "data", [])
        if _CHAT_MODEL_RE.match(m.id) and not any(x in m.id for x in _NON_CHAT)
    ]
    return sorted(set(ids), reverse=True)


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


# This machine reaches api.openai.com through a VPN tunnel (Astrill) that cuts
# any connection silent for about ten seconds. A reasoning model thinks longer
# than that before it writes a byte, so an ordinary request for a resume, even
# a streamed one, dies with "Server disconnected without sending a response"
# every time while short calls get through. Background mode never holds a
# connection open that long: see background_response_text.
#
# The tunnel also refuses the odd connect outright, so the create call is
# retried: ten attempts with the backoff capped at 30s, about three minutes.
async def _chat(
    system: str, user: str, *, model: str | None = None, attempts: int = 10
) -> str:
    client = _client()
    model = model or runtime_settings.tailor_model()
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            text = await background_response_text(
                client,
                model=model,
                instructions=system,
                input=user,
                temperature=0.4,
            )
            if not text:
                raise BackgroundResponseFailed("The model returned an empty reply.")
            return text
        except (
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
            TimeoutError,
            BackgroundResponseFailed,
        ) as exc:
            log.warning(
                "openai call failed (attempt %s/%s): %s %s",
                attempt, attempts, type(exc).__name__, exc.__cause__ or exc,
            )
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


# Who answers the tailoring questions. "api" is the OpenAI API with the key in
# .env; "chatgpt" is chatgpt.com in the person's own tab, driven from the Poong
# extension (see chatgpt_bridge), which hands its engine in as `llm`.
# Everything after the answer is the same for both.
ENGINES = ("api", "chatgpt")


class _ApiEngine:
    """Each question a fresh, stateless API call — the original path, unchanged."""

    name = "api"
    transcript: tuple[tuple[str, str], ...] = ()

    def __init__(self, model: str | None = None) -> None:
        # The model the requesting browser picked; None uses runtime.yml's.
        self.model = model

    async def ask(self, system: str, user: str, *, reply_format: str = "") -> str:
        # reply_format is for the web page, which renders Markdown; the API
        # returns text exactly as written and never needed it.
        return await _chat(system, user, model=self.model)


@asynccontextmanager
async def _open_engine(
    engine: str, llm: Any = None, model: str | None = None
) -> AsyncIterator[Any]:
    if llm is not None:
        yield llm
    elif engine == "chatgpt":
        raise RuntimeError(
            "ChatGPT resumes are started from the Poong extension in a chatgpt.com tab"
        )
    else:
        yield _ApiEngine(model)


# chatgpt.com shows its reply as rendered Markdown, so raw HTML only survives
# the trip back inside a code block. Sent to the ChatGPT engine alone.
_BUNDLE_REPLY_FORMAT = (
    "Reply format: put the complete HTML resume in one ```html code block, then the "
    "plain-text cover letter after the code block. No other commentary."
)
_MARKDOWN_REPLY_FORMAT = "Reply with the resume in Markdown only. No commentary before or after it."
_COVER_REPLY_FORMAT = "Reply with the cover letter only, as plain text. No commentary."
_ANSWERS_REPLY_FORMAT = "Reply with only the JSON object, in one ```json code block."


def _save_transcript(folder: Path, llm: Any) -> None:
    """What was sent to ChatGPT and what came back, for when a job fails."""
    turns = getattr(llm, "transcript", None)
    if not turns:
        return
    try:
        (folder / "chatgpt_raw.txt").write_text(
            "\n\n".join(f"===== {who} =====\n{text}" for who, text in turns),
            encoding="utf-8",
        )
    except OSError:
        log.warning("could not write chatgpt_raw.txt to %s", folder)


def _resume_user_message(persona, job: JobRow) -> str:
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


def _html_bundle_user_message(persona, job: JobRow) -> str:
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


def _load_persona_css(persona, accent: str | None = None) -> str:
    """The persona's stylesheet, repainted to `accent`.

    `accent` is the colour the requesting browser picked, so two people
    generating at once each get their own. None means the request did not say
    (an older client), which falls back to the last colour saved in
    runtime.yml; "" means the stylesheet's own accent.
    """
    css_name = (persona.tailor or {}).get("css_file")
    if not css_name:
        return ""
    folder = get_settings().config_dir / "persona_identity"
    path = folder / css_name
    if not path.exists():
        return ""
    css = path.read_text(encoding="utf-8")
    # A persona that does not declare the accent its sheet is written in
    # (tailor.accent_color) is left alone.
    from app.services import runtime_settings
    from app.services.resume_accent import apply_to_css

    chosen = runtime_settings.resume_accent() if accent is None else (accent or None)
    return apply_to_css(css, (persona.tailor or {}).get("accent_color"), chosen)


async def generate_resume_markdown(
    persona, job: JobRow, *, extra: str = "", llm: Any = None
) -> str:
    from app.services.resume_header import enforce_resume_header

    llm = llm or _ApiEngine()
    raw = await llm.ask(
        persona.system_prompt,
        _resume_user_message(persona, job) + extra,
        reply_format=_MARKDOWN_REPLY_FORMAT,
    )
    return enforce_resume_header(raw, persona)


async def generate_html_bundle(
    persona, job: JobRow, *, extra: str = "", llm: Any = None
) -> tuple[str, str]:
    llm = llm or _ApiEngine()
    raw = await llm.ask(
        persona.system_prompt,
        _html_bundle_user_message(persona, job) + extra,
        reply_format=_BUNDLE_REPLY_FORMAT,
    )
    html, cover = _split_html_and_cover(raw)
    # Opt-in per persona, the way the header is enforced rather than trusted:
    # the prompt asks for <strong> on the JD's keywords in every bullet, and the
    # model leaves a varying handful bare. See resume_emphasis for the numbers.
    if (persona.tailor or {}).get("enforce_bullet_emphasis"):
        from app.services.resume_emphasis import emphasise_bullets

        html = emphasise_bullets(html, job.description or "")
    return html, cover


async def generate_cover_letter(persona, job: JobRow, resume_md: str, *, llm: Any = None) -> str:
    gt = ground_truth_payload(persona)
    user = (
        f"Candidate ground truth:\n{json.dumps(gt, indent=2, ensure_ascii=False)}\n\n"
        f"Target role: {job.title} at {job.company}\n"
        f"Location: {job.location or ''}\n"
        f"JD (excerpt):\n{(job.description or '')[:8000]}\n\n"
        f"Resume draft (for tone/alignment only):\n{resume_md[:6000]}\n\n"
        "Write the cover letter plain text only."
    )
    llm = llm or _ApiEngine()
    return await llm.ask(_cover_letter_system(persona), user, reply_format=_COVER_REPLY_FORMAT)


async def generate_answers(persona, job: JobRow, *, llm: Any = None) -> dict[str, str]:
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
    llm = llm or _ApiEngine()
    raw = await llm.ask(_answers_system(), user, reply_format=_ANSWERS_REPLY_FORMAT)
    try:
        raw2 = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
        data = json.loads(raw2)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except json.JSONDecodeError:
        pass
    return {a["question"]: a["answer"] for a in persona.screening_answers if "question" in a}


def _keyword_retry_hint(breakdown) -> str:
    """Tell the next attempt exactly which terms the last one missed.

    Only terms the JD actually asks for, and only ones the persona can
    truthfully claim — the caller filters against ground truth first.
    """
    if not breakdown.missing:
        return ""
    return (
        "\n\nPREVIOUS ATTEMPT SCORED "
        f"{breakdown.score}/100 AGAINST THIS POSTING'S KEYWORD FILTER. "
        "The job description asks for these and the resume did not surface them: "
        + ", ".join(breakdown.missing[:20])
        + ". Where the candidate's real history genuinely supports a term, make it "
        "explicit in the skills list or a bullet, using the posting's own wording. "
        "Never claim experience the ground truth does not support — leave a term "
        "out rather than invent it."
    )


async def tailor_job(
    session: AsyncSession,
    job: JobRow,
    *,
    persona_id: str | None = None,
    engine: str = "api",
    llm: Any = None,
    accent: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}; expected one of {ENGINES}")
    if not (job.description and len(job.description) >= 50):
        return {"job_id": job.id, "ok": False, "reason": "not_jd_ready"}

    # The row belongs to one persona's table — that is who we tailor for. A
    # persona_id that disagrees is a caller passing the wrong row; honour the row.
    pid = job.persona_id
    if persona_id and persona_id != pid:
        log.warning(
            "tailor_job persona_id=%s but job %s is in %s's table; using the row's persona",
            persona_id, job.id, pid,
        )
    persona = load_persona(pid)
    generate_pid = persona.persona_id
    folder = ensure_job_folder(generate_pid, job.seq_no or 0, job.company)
    display = persona.display_name
    mode = (persona.tailor or {}).get("mode") or "strategist_resume"

    # The ChatGPT engine is one chat for the whole job, so every step below
    # asks inside it; the exit stack closes the engine however the job ends.
    given, llm = llm, None
    engines = AsyncExitStack()
    try:
        llm = await engines.enter_async_context(_open_engine(engine, given, model))
        if mode == "html_resume_bundle":
            resume_html, cover = await generate_html_bundle(persona, job, llm=llm)
            css = _load_persona_css(persona, accent)
            if css:
                resume_html = _inject_css(resume_html, css)
            (folder / f"{display}.html").write_text(resume_html, encoding="utf-8")
            (folder / f"{display}.md").write_text(
                f"(HTML resume — see {display}.html)\n", encoding="utf-8"
            )
            await export_pdf(resume_html, folder / f"{display}.pdf")
            resume_for_answers = resume_html
        else:
            resume_md = await generate_resume_markdown(persona, job, llm=llm)
            (folder / f"{display}.md").write_text(resume_md, encoding="utf-8")
            resume_html = markdown_to_html(resume_md, title=f"{display} — Resume")
            css = _load_persona_css(persona, accent)
            if css:
                resume_html = polish_flat_resume_html(resume_html, display_name=display)
                resume_html = replace_document_css(resume_html, css)
            (folder / f"{display}.html").write_text(resume_html, encoding="utf-8")
            await export_pdf(resume_html, folder / f"{display}.pdf")
            cover = await generate_cover_letter(persona, job, resume_md, llm=llm)
            resume_for_answers = resume_md

        floor = runtime_settings.min_ats_score()
        breakdown = score_resume(resume_for_answers, job.description, job_title=job.title)
        attempts = 1
        if floor:
            max_attempts = runtime_settings.max_attempts()
            # Only chase terms the persona can actually back up.
            truthful = extract_terms(persona_corpus(persona))
            while breakdown.score < floor and attempts < max_attempts:
                chase = [m for m in breakdown.missing if m in truthful]
                if not chase:
                    log.info(
                        "ats floor %s unreachable for job_id=%s: missing terms are not "
                        "in this persona's history (%s)",
                        floor, job.id, ", ".join(breakdown.missing[:8]),
                    )
                    break
                attempts += 1
                log.info(
                    "ats retry %s/%s job_id=%s score=%s floor=%s",
                    attempts, max_attempts, job.id, breakdown.score, floor,
                )
                hint = _keyword_retry_hint(breakdown)
                if mode == "html_resume_bundle":
                    resume_html, cover = await generate_html_bundle(
                        persona, job, extra=hint, llm=llm
                    )
                    css = _load_persona_css(persona, accent)
                    if css:
                        resume_html = _inject_css(resume_html, css)
                    (folder / f"{display}.html").write_text(resume_html, encoding="utf-8")
                    await export_pdf(resume_html, folder / f"{display}.pdf")
                    resume_for_answers = resume_html
                else:
                    resume_md = await generate_resume_markdown(
                        persona, job, extra=hint, llm=llm
                    )
                    (folder / f"{display}.md").write_text(resume_md, encoding="utf-8")
                    resume_html = markdown_to_html(resume_md, title=f"{display} — Resume")
                    css = _load_persona_css(persona, accent)
                    if css:
                        resume_html = polish_flat_resume_html(resume_html, display_name=display)
                        resume_html = replace_document_css(resume_html, css)
                    (folder / f"{display}.html").write_text(resume_html, encoding="utf-8")
                    await export_pdf(resume_html, folder / f"{display}.pdf")
                    resume_for_answers = resume_md
                breakdown = score_resume(
                    resume_for_answers, job.description, job_title=job.title
                )
        # Does the finished document hold together as a truthful resume?
        credibility = score_credibility(resume_for_answers)
        score_payload = {
            **breakdown.as_dict(),
            "attempts": attempts,
            "floor": floor,
            "engine": engine,
        }
        (folder / "ats_score.json").write_text(
            json.dumps(
                {"ats": score_payload, "credibility": credibility.as_dict()},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        answers = await generate_answers(persona, job, llm=llm)
        (folder / "answers.json").write_text(
            json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (folder / "questions.json").write_text(
            json.dumps(list(answers.keys()), indent=2, ensure_ascii=False), encoding="utf-8"
        )

        if not cover:
            cover = await generate_cover_letter(persona, job, resume_for_answers[:8000], llm=llm)
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
            status=JobStatus.RESUME_READY.value,
            description=job.description,
            job_id=job.id,
            extra={"tailored": True, "persona_id": generate_pid},
        )
        # Fold the result straight onto the persona's own row.
        job.folder_path = str(folder)
        job.ats_score = breakdown.score
        job.ats_breakdown = json.dumps(score_payload, ensure_ascii=False)
        job.credibility_score = credibility.score
        job.credibility_findings = json.dumps(credibility.as_dict(), ensure_ascii=False)
        if (job.notes or "").startswith("tailor_failed:"):
            job.notes = None
        _set_row_status(job, JobStatus.RESUME_READY.value)
        log.info(
            "tailor ok job_id=%s seq=%s persona=%s engine=%s folder=%s",
            job.id,
            job.seq_no,
            generate_pid,
            engine,
            folder,
        )
        await session.flush()
        return {
            "job_id": job.id,
            "ok": True,
            "engine": engine,
            "status": job.status,
            "folder": str(folder),
            "persona_id": generate_pid,
            "ats_score": breakdown.score,
            "ats_breakdown": score_payload,
            "credibility_score": credibility.score,
            "credibility": credibility.as_dict(),
            "package": {
                "id": job.id,
                "persona_id": job.persona_id,
                "status": job.status,
                "folder_path": job.folder_path,
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
        _set_row_status(job, JobStatus.TAILOR_FAILED.value)
        job.notes = f"tailor_failed:{reason}"
        await session.flush()
        return {
            "job_id": job.id,
            "ok": False,
            "engine": engine,
            "reason": reason,
            "persona_id": generate_pid,
            "package": {
                "id": job.id,
                "persona_id": job.persona_id,
                "status": job.status,
                "folder_path": job.folder_path,
                "notes": job.notes,
            },
        }
    finally:
        _save_transcript(folder, llm)
        try:
            await engines.aclose()
        except Exception:  # noqa: BLE001
            log.exception("closing the %s engine failed job_id=%s", engine, job.id)


def build_tailor_prompt(persona, job: JobRow) -> dict[str, str]:
    """The exact system + user messages tailor_job would send for this job.

    Lets the UI show (and copy) the full prompt, so a resume can be generated by
    hand in a chat window and pasted back through build_package_from_text when
    the API path is unavailable. The system prompt is the persona's own
    .prompt.md; the user message carries the JD and the immutable ground truth.
    """
    mode = (persona.tailor or {}).get("mode") or "strategist_resume"
    system = persona.system_prompt
    if mode == "html_resume_bundle":
        user = _html_bundle_user_message(persona, job)
    else:
        user = _resume_user_message(persona, job)
    return {"mode": mode, "system": system, "user": user}


async def build_package_from_text(
    session: AsyncSession,
    job: JobRow,
    *,
    persona_id: str,
    raw_text: str,
    engine_label: str = "manual_paste",
    accent: str | None = None,
) -> dict[str, Any]:
    """Build the resume + cover package from text pasted by hand, no LLM call.

    This is the post-generation half of tailor_job: it takes the chat output a
    person produced from build_tailor_prompt, writes the same files (HTML, PDF,
    ats_score.json, answers from the persona's static screening_answers, cover
    letter when the paste carried one), scores it, and folds the result onto the
    persona's own row as resume_ready. No API key and no network are touched.
    """
    persona = load_persona(job.persona_id)
    generate_pid = persona.persona_id
    display = persona.display_name
    mode = (persona.tailor or {}).get("mode") or "strategist_resume"
    jd = job.description or ""

    # Parse and validate the paste BEFORE creating any files, so a bad paste
    # (missing or truncated HTML) fails without leaving an empty job folder.
    if mode == "html_resume_bundle":
        resume_html, cover = _split_html_and_cover(raw_text)
        if (persona.tailor or {}).get("enforce_bullet_emphasis"):
            from app.services.resume_emphasis import emphasise_bullets

            resume_html = emphasise_bullets(resume_html, jd)
    else:
        from app.services.resume_header import enforce_resume_header

        resume_md = enforce_resume_header(raw_text.strip(), persona)
        # Markdown personas generate the cover in a second call; a single paste
        # carries the resume only, so the cover is left for the answers/cover step.
        cover = ""

    folder = ensure_job_folder(generate_pid, job.seq_no or 0, job.company)

    if mode == "html_resume_bundle":
        css = _load_persona_css(persona, accent)
        if css:
            resume_html = _inject_css(resume_html, css)
        (folder / f"{display}.html").write_text(resume_html, encoding="utf-8")
        (folder / f"{display}.md").write_text(
            f"(HTML resume — see {display}.html)\n", encoding="utf-8"
        )
        await export_pdf(resume_html, folder / f"{display}.pdf")
        resume_for_answers = resume_html
    else:
        (folder / f"{display}.md").write_text(resume_md, encoding="utf-8")
        resume_html = markdown_to_html(resume_md, title=f"{display} — Resume")
        css = _load_persona_css(persona, accent)
        if css:
            resume_html = polish_flat_resume_html(resume_html, display_name=display)
            resume_html = replace_document_css(resume_html, css)
        (folder / f"{display}.html").write_text(resume_html, encoding="utf-8")
        await export_pdf(resume_html, folder / f"{display}.pdf")
        resume_for_answers = resume_md

    floor = runtime_settings.min_ats_score()
    breakdown = score_resume(resume_for_answers, jd, job_title=job.title)
    credibility = score_credibility(resume_for_answers)
    score_payload = {
        **breakdown.as_dict(),
        "attempts": 1,
        "floor": floor,
        "engine": engine_label,
    }
    (folder / "ats_score.json").write_text(
        json.dumps(
            {"ats": score_payload, "credibility": credibility.as_dict()},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # No LLM here: answers come straight from the persona's static screening_answers.
    answers = {
        str(a["question"]): str(a.get("answer", ""))
        for a in (persona.screening_answers or [])
        if a.get("question")
    }
    (folder / "answers.json").write_text(
        json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (folder / "questions.json").write_text(
        json.dumps(list(answers.keys()), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if cover:
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
        status=JobStatus.RESUME_READY.value,
        description=job.description,
        job_id=job.id,
        extra={"tailored": True, "persona_id": generate_pid, "source": engine_label},
    )
    job.folder_path = str(folder)
    job.ats_score = breakdown.score
    job.ats_breakdown = json.dumps(score_payload, ensure_ascii=False)
    job.credibility_score = credibility.score
    job.credibility_findings = json.dumps(credibility.as_dict(), ensure_ascii=False)
    if (job.notes or "").startswith("tailor_failed:"):
        job.notes = None
    _set_row_status(job, JobStatus.RESUME_READY.value)
    await session.flush()
    log.info(
        "manual build ok job_id=%s seq=%s persona=%s cover=%s folder=%s",
        job.id,
        job.seq_no,
        generate_pid,
        bool(cover),
        folder,
    )
    return {
        "job_id": job.id,
        "ok": True,
        "engine": engine_label,
        "status": job.status,
        "folder": str(folder),
        "persona_id": generate_pid,
        "cover_included": bool(cover),
        "ats_score": breakdown.score,
        "ats_breakdown": score_payload,
        "credibility_score": credibility.score,
        "credibility": credibility.as_dict(),
        "package": {
            "id": job.id,
            "persona_id": job.persona_id,
            "status": job.status,
            "folder_path": job.folder_path,
        },
    }
