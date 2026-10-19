"""Per-person disable: each persona owns its own copy of a job, with its own
``enabled`` tri-state, so applying closes a company's *other* openings for that
persona only.

When a persona has applied to a job at company X (its own row, status=applied),
X's other openings in that persona's table come back marked disabled (reason
"company_applied") — the row the persona applied to stays active. A persona can
also disable (enabled=False) or force-on (enabled=True) any of its rows by hand.
Ingestion and the resume-build batch leave disabled rows alone. Another persona's
table is untouched.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.config import get_settings
from app.db import session as sess
from app.db.enums import JobStatus
from app.db.job_tables import job_model
from app.db.models import utcnow
from app.db.session import get_session_factory, init_db
from app.services.pipeline import disabled_job_ids_for, process_listing

PROFILE = "alex_mak"  # the viewing persona that applies
OTHER = "brian_gou"  # a persona that does not


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    db_file = tmp_path / "disabled.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()
    return get_session_factory()


async def _add(session, company: str, url: str, title: str = "Data Engineer", pid=PROFILE):
    result = await process_listing(
        session,
        {"company": company, "title": title, "url": url, "location": "Remote, US"},
        persona_id=pid,
        skip_content_gates=True,
    )
    await session.commit()
    assert result.ok, result.reason
    return result.job


async def _apply(session, job) -> None:
    """Record an apply straight on the persona's own row."""
    job.status = JobStatus.APPLIED.value
    job.applied_at = utcnow()
    await session.commit()


async def _set_enabled(session, job, value) -> None:
    job.enabled = value
    await session.commit()


async def _list(session, **kwargs):
    from app.api.jobs import list_jobs

    params = dict(
        page=1,
        page_size=50,
        offset=None,
        limit=None,
        country=None,
        hide_own_employers=False,
        sort=None,
        sort_dir="asc",
        db=session,
    )
    params.update(kwargs)
    return await list_jobs(**params)


async def _by_seq(session, **kwargs) -> dict[int, dict]:
    out = await _list(session, **kwargs)
    return {j["seq_no"]: j for j in out["jobs"]}


async def _age(session, job, days: int = 11) -> None:
    """Push a listing past the one-per-company window so a sibling can ingest."""
    job.created_at = utcnow() - timedelta(days=days)
    await session.commit()


async def _three_jobs(session, pid=PROFILE):
    """a and b at company X, c at company Y, all in pid's table. Returns (a, b, c)."""
    a = await _add(session, "Acme", "https://example.com/a", "Data Engineer", pid=pid)
    await _age(session, a)
    b = await _add(session, "Acme", "https://example.com/b", "ML Engineer", pid=pid)
    c = await _add(session, "Beta", "https://example.com/c", "Backend Engineer", pid=pid)
    return a, b, c


async def test_the_sibling_is_shown_but_disabled(factory):
    async with factory() as session:
        a, _b, _c = await _three_jobs(session)
        await _apply(session, a)

        jobs = await _by_seq(session, profile=PROFILE)
        # Nothing is hidden — all three are present.
        assert sorted(jobs) == [1, 2, 3]
        # The applied job stays active; its Acme sibling is disabled; Beta is not.
        assert jobs[1]["disabled"] is False
        assert jobs[2]["disabled"] is True
        assert jobs[2]["disabled_reason"] == "company_applied"
        assert jobs[3]["disabled"] is False

        out = await _list(session, profile=PROFILE)
        assert out["disabled_count"] == 1


async def test_a_hand_disabled_job_reads_as_manual(factory):
    async with factory() as session:
        _a, _b, c = await _three_jobs(session)
        await _set_enabled(session, c, False)

        jobs = await _by_seq(session, profile=PROFILE)
        assert jobs[3]["disabled"] is True
        assert jobs[3]["disabled_reason"] == "manual"


async def test_enabling_a_sibling_overrides_the_auto_disable(factory):
    async with factory() as session:
        a, b, _c = await _three_jobs(session)
        await _apply(session, a)
        # b is auto-disabled as an Acme sibling; force it back on.
        await _set_enabled(session, b, True)

        jobs = await _by_seq(session, profile=PROFILE)
        assert jobs[2]["disabled"] is False


async def test_the_disable_and_enable_endpoints(factory):
    from app.api.jobs import DisableBody, disable_job, enable_job

    async with factory() as session:
        _a, _b, c = await _three_jobs(session)
        res = await disable_job(c.id, DisableBody(persona_id=PROFILE), session)
        assert res["disabled"] is True and res["disabled_reason"] == "manual"
        res = await enable_job(c.id, DisableBody(persona_id=PROFILE), session)
        assert res["disabled"] is False


async def test_an_apply_older_than_30_days_stops_disabling(factory):
    async with factory() as session:
        a, _b, _c = await _three_jobs(session)
        old = utcnow() - timedelta(days=31)
        a.status = JobStatus.APPLIED.value
        a.applied_at = old
        await session.commit()

        jobs = await _by_seq(session, profile=PROFILE)
        assert jobs[2]["disabled"] is False  # sibling no longer auto-disabled


async def test_another_profile_sees_nothing_disabled(factory):
    async with factory() as session:
        a, _b, _c = await _three_jobs(session)
        await _apply(session, a)
        # Brian has his own copies; Alex's apply does not touch Brian's table.
        await _three_jobs(session, pid=OTHER)

        jobs = await _by_seq(session, profile=OTHER)
        assert all(not jobs[s]["disabled"] for s in (1, 2, 3))


async def test_resume_build_skips_disabled_jobs(factory):
    async with factory() as session:
        a, b, c = await _three_jobs(session)
        await _apply(session, a)
        await _set_enabled(session, c, False)

        ids = await disabled_job_ids_for(session, PROFILE)
        assert b.id in ids  # auto applied-company sibling
        assert c.id in ids  # hand-disabled
        assert a.id not in ids  # the applied job is not a disabled sibling


async def test_ingestion_ignores_a_disabled_duplicate(factory):
    async with factory() as session:
        first = await _add(session, "Acme", "https://example.com/a")
        # A second Acme job inside the 7-day window is normally duplicate_company.
        await _set_enabled(session, first, False)
        r2 = await process_listing(
            session,
            {
                "company": "Acme",
                "title": "ML Engineer",
                "url": "https://example.com/b",
                "location": "Remote, US",
            },
            persona_id=PROFILE,
            skip_content_gates=True,
        )
        await session.commit()
        assert r2.ok, r2.reason
