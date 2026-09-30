"""Resume generation through chatgpt.com, driven from the Poong extension.

The person opens chatgpt.com in their own browser, opens the Poong panel
there, ticks the jobs to write resumes for, and presses Start. From then on
the extension does the asking: for each job it starts a session here, takes
the prompts one at a time, pastes each into that tab's chat, and posts
ChatGPT's reply back.

The backend side is the ordinary tailor_job, with an engine whose ask()
hands the prompt to the extension and waits for the reply. So a ChatGPT
resume goes through exactly the steps an API resume does — the HTML and PDF,
cover letter, answers.json, the ATS score and its retries — and a job takes
as many prompts as that path asks for, which the extension never needs to
know in advance.

Nothing here reaches out to a browser. An earlier version had the backend
hand prompts to a polling extension; Chrome stops extension workers when it
likes, and prompts went missing with them. Here the work lives in the tab
the person is looking at, and the backend only answers.

State lives in memory. A backend restart ends every session, and the
extension reports those jobs as failed.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services import runtime_settings

# uvicorn's own logger: the app loggers do not reach the server's log files.
log = logging.getLogger("uvicorn.error")

# Pasting, sending and reading the reply happen in the page, on top of the
# time ChatGPT is given to answer.
_PAGE_GRACE_SECONDS = 120.0
# Between two prompts the backend writes files and a PDF. The extension waits
# this long per call, then asks again.
_STEP_WAIT_SECONDS = 60.0
# Finished sessions nobody collected are dropped after this.
_FORGET_AFTER_SECONDS = 15 * 60.0

# Replies ChatGPT sends instead of an answer. Short, so a resume that happens
# to mention a "limit" is never mistaken for one.
_REFUSAL_RE = re.compile(
    r"reached (?:the|your|our) .{0,40}limit|usage cap|too many requests|"
    r"something went wrong|error generating a response",
    re.I,
)
_REFUSAL_MAX_CHARS = 600

_FENCE_RE = re.compile(r"^\s*```[\w+-]*[ \t]*\n(.*?)\n\s*```\s*$", re.S)

# ChatGPT's own citation markers. On the page they draw as a small source
# chip; a copied message carries them as raw text, and one ended up printed at
# the foot of a cover letter. Seen as :chatgpt-content-reference{index="0"},
# the older :contentReference[oaicite:0]{index=0}, and 【4†source】.
_CITATION_RE = re.compile(
    r"[ \t]*:{1,2}(?:chatgpt-)?content-?reference(?:\[[^\]\n]*\])?(?:\{[^}\n]*\})?"
    r"|[ \t]*【[^】\n]{0,80}†[^】\n]{0,80}】",
    re.I,
)


def _strip_citations(text: str) -> str:
    cleaned = _CITATION_RE.sub("", text or "")
    # A marker alone on its line leaves the line behind, empty.
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", cleaned).strip()


class ChatGPTBridgeError(RuntimeError):
    pass


def _unwrap_single_fence(text: str) -> str:
    """A reply that is nothing but one code block, without the fence."""
    m = _FENCE_RE.match(text or "")
    if m and "```" not in m.group(1):
        return m.group(1)
    return text


def _expect(reply_format: str) -> str:
    """What a good reply contains, so the extension can tell it from the prompt."""
    if "```html" in reply_format:
        return "html"
    if "json" in reply_format.lower():
        return "json"
    return "text"


@dataclass
class _Session:
    job_id: int
    persona_id: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: float = field(default_factory=time.monotonic)
    # The prompt waiting for a reply, its number, and what it should return.
    prompt: str | None = None
    expect: str = "text"
    step: int = 0
    delivered: int = 0
    reply: asyncio.Future | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    done: bool = False
    finished_at: float = 0.0
    result: dict[str, Any] | None = None
    error: str = ""
    transcript: list[tuple[str, str]] = field(default_factory=list)


_sessions: dict[str, _Session] = {}


class ExtensionChat:
    """The tailor engine for one job: every question goes to the extension."""

    name = "chatgpt"

    def __init__(self, session: _Session) -> None:
        self._s = session

    @property
    def transcript(self) -> list[tuple[str, str]]:
        return self._s.transcript

    async def ask(self, system: str, user: str, *, reply_format: str = "") -> str:
        s = self._s
        # The page has no system role; the instructions simply go first.
        text = "\n\n".join(p.strip() for p in (system, user, reply_format) if p and p.strip())
        s.transcript.append(("sent", text))
        s.reply = asyncio.get_running_loop().create_future()
        s.prompt = text
        s.expect = _expect(reply_format)
        s.step += 1
        s.changed.set()
        timeout = runtime_settings.chatgpt_timeout_seconds() + _PAGE_GRACE_SECONDS
        try:
            reply = await asyncio.wait_for(s.reply, timeout)
        except asyncio.TimeoutError as exc:
            raise ChatGPTBridgeError(
                f"chatgpt_timeout: no reply from the ChatGPT tab within {int(timeout)}s"
            ) from exc
        finally:
            s.prompt = None
        reply = _strip_citations(reply)
        if not reply:
            raise ChatGPTBridgeError("chatgpt_empty_reply")
        if len(reply) <= _REFUSAL_MAX_CHARS and _REFUSAL_RE.search(reply):
            raise ChatGPTBridgeError(f"chatgpt_refused: {reply[:300]}")
        s.transcript.append(("chatgpt", reply))
        return _unwrap_single_fence(reply)


async def _run(s: _Session) -> None:
    from app.db.models import Job
    from app.db.session import get_session_factory
    from app.services.tailor import tailor_job

    try:
        async with get_session_factory()() as db:
            job = await db.get(Job, s.job_id)
            if job is None:
                s.error = "job_missing"
                return
            s.result = await tailor_job(
                db, job, persona_id=s.persona_id, engine="chatgpt", llm=ExtensionChat(s)
            )
            await db.commit()
    except asyncio.CancelledError:
        s.error = "cancelled"
    except Exception as exc:  # noqa: BLE001
        log.exception("chatgpt: session %s for job %s crashed", s.id[:8], s.job_id)
        s.error = str(exc)[:400]
    finally:
        s.done = True
        s.finished_at = time.monotonic()
        s.changed.set()


def _forget_stale() -> None:
    now = time.monotonic()
    for sid, s in list(_sessions.items()):
        if s.done and now - s.finished_at > _FORGET_AFTER_SECONDS:
            _sessions.pop(sid, None)


def _step_payload(s: _Session) -> dict[str, Any]:
    if s.prompt is not None and s.step > s.delivered:
        s.delivered = s.step
        return {
            "session": s.id,
            "step": s.step,
            "prompt": s.prompt,
            "expect": s.expect,
            # A job's first prompt opens a new chat; the rest follow in it.
            "new_chat": s.step == 1,
            "timeout": runtime_settings.chatgpt_timeout_seconds(),
        }
    if s.done:
        _sessions.pop(s.id, None)
        r = s.result or {}
        return {
            "session": s.id,
            "done": True,
            "ok": bool(r.get("ok")),
            "reason": r.get("reason") or s.error or None,
            "ats_score": r.get("ats_score"),
            "folder": r.get("folder"),
        }
    return {"session": s.id, "pending": True}


async def _next_step(s: _Session) -> dict[str, Any]:
    """The next prompt, or the outcome, waiting up to _STEP_WAIT_SECONDS for either."""
    deadline = time.monotonic() + _STEP_WAIT_SECONDS
    while True:
        s.changed.clear()
        payload = _step_payload(s)
        if not payload.get("pending"):
            return payload
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return payload
        try:
            await asyncio.wait_for(s.changed.wait(), remaining)
        except asyncio.TimeoutError:
            pass


def _get(session_id: str) -> _Session:
    s = _sessions.get(session_id)
    if s is None:
        raise ChatGPTBridgeError(
            "chatgpt_session_gone: the backend no longer has this job's session "
            "(it restarted, or the job already finished)"
        )
    return s


async def start(job_id: int, persona_id: str) -> dict[str, Any]:
    """Begin one job; answers with its first prompt."""
    _forget_stale()
    s = _Session(job_id=job_id, persona_id=persona_id)
    _sessions[s.id] = s
    s.task = asyncio.create_task(_run(s), name=f"chatgpt-{s.id[:8]}")
    log.info("chatgpt: session %s started for job %s as %s", s.id[:8], job_id, persona_id)
    return await _next_step(s)


async def reply(session_id: str, step: int, text: str) -> dict[str, Any]:
    """ChatGPT's answer to prompt `step`; answers with the next prompt or the outcome."""
    s = _get(session_id)
    if step == s.step and s.reply is not None and not s.reply.done():
        s.reply.set_result(text)
    return await _next_step(s)


async def fail(session_id: str, step: int, error: str) -> dict[str, Any]:
    """The page could not produce a reply to prompt `step`; the job fails with why."""
    s = _get(session_id)
    if step == s.step and s.reply is not None and not s.reply.done():
        s.reply.set_exception(ChatGPTBridgeError(error or "chatgpt_failed"))
    return await _next_step(s)


async def next_step(session_id: str) -> dict[str, Any]:
    return await _next_step(_get(session_id))


def cancel(session_id: str) -> bool:
    s = _sessions.get(session_id)
    if s is None:
        return False
    if s.reply is not None and not s.reply.done():
        s.reply.set_exception(ChatGPTBridgeError("cancelled: stopped from the Poong panel"))
    elif s.task is not None and not s.task.done():
        s.task.cancel()
    return True


def status() -> dict[str, Any]:
    return {
        "running": sum(1 for s in _sessions.values() if not s.done),
        "timeout_seconds": runtime_settings.chatgpt_timeout_seconds(),
    }
