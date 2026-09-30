from __future__ import annotations

import asyncio

import pytest

from app.services import chatgpt_bridge as bridge
from app.services import tailor


@pytest.fixture
async def job_id(tmp_path, monkeypatch):
    """A fresh database holding one job with a usable description."""
    from app.config import get_settings
    from app.db import session as sess
    from app.db.session import get_session_factory, init_db
    from app.services.pipeline import process_listing

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'gpt.db').as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()
    async with get_session_factory()() as db:
        r = await process_listing(
            db,
            {
                "company": "Acme",
                "title": "Data Engineer",
                "url": "https://example.com/jobs/1",
                "location": "Remote, US",
                "description": "We need Python and SQL. " * 10,
            },
            skip_content_gates=True,
        )
        await db.commit()
        yield r.job.id
    bridge._sessions.clear()


def _fake_tailor(asks: list[tuple[str, str, str]], outcome: dict):
    """A tailor_job that asks the given questions through the engine it is handed."""

    async def fake(db, job, *, persona_id=None, engine="api", llm=None):
        assert engine == "chatgpt" and llm is not None
        replies = [await llm.ask(system, user, reply_format=fmt) for system, user, fmt in asks]
        return {**outcome, "job_id": job.id, "replies": replies}

    return fake


async def test_a_job_hands_out_its_prompts_in_order_then_its_outcome(job_id, monkeypatch):
    asks = [
        ("PERSONA PROMPT", "JOB", tailor._BUNDLE_REPLY_FORMAT),
        ("ANSWERS", "QUESTIONS", tailor._ANSWERS_REPLY_FORMAT),
    ]
    monkeypatch.setattr(
        tailor, "tailor_job", _fake_tailor(asks, {"ok": True, "ats_score": 88, "folder": "F"})
    )

    first = await bridge.start(job_id, "bruno_raphael")
    assert first["step"] == 1 and first["new_chat"] is True and first["expect"] == "html"
    # No system role on the page: the instructions go first, then the format.
    assert first["prompt"].startswith("PERSONA PROMPT\n\nJOB\n\nReply format")

    second = await bridge.reply(first["session"], 1, "```html\n<html></html>\n```")
    assert second["step"] == 2 and second["new_chat"] is False and second["expect"] == "json"

    done = await bridge.reply(first["session"], 2, '```json\n{"q": "a"}\n```')
    assert done == {
        "session": first["session"],
        "done": True,
        "ok": True,
        "reason": None,
        "ats_score": 88,
        "folder": "F",
    }
    assert first["session"] not in bridge._sessions


async def test_a_page_failure_fails_the_job_with_its_reason(job_id, monkeypatch):
    # The real tailor_job: its own error handling turns this into a failed package.
    first = await bridge.start(job_id, "bruno_raphael")
    done = await bridge.fail(first["session"], first["step"], "chatgpt_send_failed: never sent")
    assert done["done"] is True and done["ok"] is False
    assert "chatgpt_send_failed" in done["reason"]


async def test_a_limit_message_is_not_taken_for_a_resume(job_id, monkeypatch):
    monkeypatch.setattr(tailor, "tailor_job", _fake_tailor([("s", "u", "")], {"ok": True}))
    first = await bridge.start(job_id, "bruno_raphael")
    done = await bridge.reply(
        first["session"], 1, "You've reached our limit of messages. Try again later."
    )
    assert done["ok"] is False and "chatgpt_refused" in done["reason"]


async def test_stopping_from_the_panel_ends_the_job(job_id, monkeypatch):
    monkeypatch.setattr(tailor, "tailor_job", _fake_tailor([("s", "u", "")], {"ok": True}))
    first = await bridge.start(job_id, "bruno_raphael")
    assert bridge.cancel(first["session"])
    done = await bridge.next_step(first["session"])
    assert done["ok"] is False and "cancelled" in done["reason"]


async def test_a_stale_reply_is_ignored(job_id, monkeypatch):
    monkeypatch.setattr(tailor, "tailor_job", _fake_tailor([("s", "u", "")], {"ok": True}))
    first = await bridge.start(job_id, "bruno_raphael")
    monkeypatch.setattr(bridge, "_STEP_WAIT_SECONDS", 0.3)
    # A reply for a step that is not the current one changes nothing.
    pending = await asyncio.wait_for(bridge.reply(first["session"], 7, "late"), 5)
    assert pending == {"session": first["session"], "pending": True}
    done = await bridge.reply(first["session"], 1, "the answer")
    assert done["ok"] is True


async def test_an_unknown_session_says_so():
    with pytest.raises(bridge.ChatGPTBridgeError, match="chatgpt_session_gone"):
        await bridge.next_step("nope")


def test_unwrap_single_fence():
    assert bridge._unwrap_single_fence("```json\n{\"a\": 1}\n```") == '{"a": 1}'
    assert bridge._unwrap_single_fence("plain text") == "plain text"
    # A code block followed by prose is two things; left alone.
    mixed = "```html\n<html></html>\n```\n\nDear team,"
    assert bridge._unwrap_single_fence(mixed) == mixed


def test_chatgpt_citation_markers_are_removed():
    letter = (
        "I would welcome the opportunity.\n"
        "Thank you for your time and consideration.\n"
        "Brian(Yi) Gou\n"
        ':chatgpt-content-reference{index="0"}'
    )
    assert bridge._strip_citations(letter).endswith("Brian(Yi) Gou")
    assert (
        bridge._strip_citations("Cut runtime by 40% :contentReference[oaicite:3]{index=3}.")
        == "Cut runtime by 40%."
    )
    assert bridge._strip_citations("Built on AWS 【4†source】 and Spark.") == "Built on AWS and Spark."
    # Ordinary text with braces and colons is left alone.
    plain = 'The JSON: {"a": 1} and a note: see reference manual.'
    assert bridge._strip_citations(plain) == plain


async def test_markers_never_reach_the_resume(job_id, monkeypatch):
    monkeypatch.setattr(tailor, "tailor_job", _fake_tailor([("s", "u", "")], {"ok": True}))
    first = await bridge.start(job_id, "bruno_raphael")
    session = bridge._sessions[first["session"]]
    await bridge.reply(first["session"], 1, 'Dear team,\nBrian\n:chatgpt-content-reference{index="0"}')
    assert session.transcript[-1] == ("chatgpt", "Dear team,\nBrian")


def test_what_each_reply_should_hold():
    assert bridge._expect(tailor._BUNDLE_REPLY_FORMAT) == "html"
    assert bridge._expect(tailor._ANSWERS_REPLY_FORMAT) == "json"
    assert bridge._expect(tailor._COVER_REPLY_FORMAT) == "text"


class _FakeChat:
    name = "chatgpt"

    def __init__(self, reply: str):
        self.reply = reply
        self.formats: list[str] = []

    async def ask(self, system, user, *, reply_format=""):
        self.formats.append(reply_format)
        return self.reply


async def test_html_bundle_parses_a_fenced_chatgpt_reply(monkeypatch):
    class Persona:
        system_prompt = "prompt.md"
        tailor = {}
        identity = {}

    class Job:
        description = "We need Python. " * 10
        title = "Engineer"
        company = "Acme"
        location = "Remote"
        url = "https://example.com"

    reply = (
        "```html\n<!DOCTYPE html><html><body><p>Resume</p></body></html>\n```\n\n"
        "Dear Acme team,\nI would like to apply."
    )
    chat = _FakeChat(reply)
    monkeypatch.setattr(tailor, "ground_truth_payload", lambda persona, **_: {})
    html, cover = await tailor.generate_html_bundle(Persona(), Job(), llm=chat)
    assert html.startswith("<!DOCTYPE html>") and html.endswith("</html>")
    assert cover.startswith("Dear Acme team")
    assert chat.formats == [tailor._BUNDLE_REPLY_FORMAT]


async def test_api_engine_sends_exactly_what_it_always_did(monkeypatch):
    sent = []

    async def fake_chat(system, user, **_):
        sent.append((system, user))
        return "ok"

    monkeypatch.setattr(tailor, "_chat", fake_chat)
    engine = tailor._ApiEngine()
    assert await engine.ask("SYS", "USER", reply_format="ignored for the API") == "ok"
    assert sent == [("SYS", "USER")]


async def test_chatgpt_needs_the_extension_to_hand_its_engine_in():
    with pytest.raises(RuntimeError, match="Poong extension"):
        async with tailor._open_engine("chatgpt"):
            pass


async def test_unknown_engine_is_refused():
    with pytest.raises(ValueError, match="unknown engine"):
        await tailor.tailor_job(None, None, engine="bard")  # type: ignore[arg-type]
