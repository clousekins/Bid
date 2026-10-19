from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session as sess
from app.db.job_tables import job_model
from app.db.models import Application, utcnow
from app.db.session import get_session_factory, init_db
from app.services.pipeline import process_listing
from app.validators.dedupe import normalize_company

# The jobs table is the persona now, so a listing is ingested for one named
# person and lives in that person's own ``jobs__{pid}`` table.
PID = "phong_v_cao"
OTHER = "brian_gou"


async def _fresh_db(tmp_path, monkeypatch, name: str = "test.db") -> None:
    db_file = tmp_path / name
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()


async def _list_jobs(session, **kwargs):
    """Call the list_jobs endpoint handler directly, filling its Query defaults."""
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


@pytest.mark.asyncio
async def test_pipeline_seq_and_applied_skip(tmp_path, monkeypatch):
    # The company-already-applied skip is per persona now: a persona is blocked
    # from ingesting another opening at a company only once *that persona* has
    # applied there. Another person's apply never closes the company here —
    # each person owns their own copy of the listing.
    await _fresh_db(tmp_path, monkeypatch)

    factory = get_session_factory()
    model = job_model(PID)
    payloads = [
        {
            "company": "Acme",
            "title": "Senior Software Engineer",
            "url": "https://boards.greenhouse.io/acme/jobs/1",
            "location": "Remote, US",
        },
        {
            "company": "Beta",
            "title": "Staff ML Engineer",
            "url": "https://boards.greenhouse.io/beta/jobs/2",
            "location": "Remote, United States",
        },
        {
            "company": "Gamma",
            "title": "Backend Engineer",
            "url": "https://example.com/jobs/3",
            "location": "Remote US",
        },
    ]
    async with factory() as session:
        for body in payloads:
            result = await process_listing(
                session, body, persona_id=PID, skip_content_gates=True
            )
            assert result.ok, result.reason
        await session.commit()
        rows = (
            await session.execute(select(model).order_by(model.seq_no))
        ).scalars().all()
        # seq_no is numbered within this persona's own table.
        assert [j.seq_no for j in rows] == [1, 2, 3]
        assert all(j.status == "new" for j in rows)

        acme = rows[0]
        # Clear the one-per-company re-insert window so the applied gate is what
        # the next ingest actually turns on.
        acme.created_at = utcnow() - timedelta(days=11)
        normalized_acme = normalize_company(acme.company)
        await session.commit()

        # Another person applying at Acme does not close it for PID.
        session.add(
            Application(
                persona_id=OTHER,
                company=acme.company,
                normalized_company=normalized_acme,
                job_id=acme.id,
                status="applied",
            )
        )
        await session.commit()

        r_other = await process_listing(
            session,
            {
                "company": "Acme LLC",
                "title": "Staff Engineer",
                "url": "https://example.com/b",
                "location": "Remote, US",
            },
            persona_id=PID,
            skip_content_gates=True,
        )
        await session.commit()
        assert r_other.ok, r_other.reason

        # Once PID has applied at the company, it is closed to ingest for PID.
        session.add(
            Application(
                persona_id=PID,
                company=acme.company,
                normalized_company=normalized_acme,
                job_id=acme.id,
                status="applied",
            )
        )
        await session.commit()

        r2 = await process_listing(
            session,
            {
                "company": "Acme LLC",
                "title": "Principal Engineer",
                "url": "https://example.com/c",
                "location": "Remote, US",
            },
            persona_id=PID,
            skip_content_gates=True,
        )
        await session.commit()
        assert r2.skipped
        assert r2.reason == "company_already_applied"

    sess._engine = None
    sess._session_factory = None
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_list_jobs_is_per_profile_and_stats_aggregate(tmp_path, monkeypatch):
    """list_jobs selects one persona's table by ``profile``; the stats endpoint
    aggregates across every persona table."""
    await _fresh_db(tmp_path, monkeypatch, name="smoke.db")

    factory = get_session_factory()
    async with factory() as session:
        for i in range(2):
            r = await process_listing(
                session,
                {
                    "company": f"Co{i}",
                    "title": "Data Engineer",
                    "url": f"https://example.com/pid/{i}",
                    "location": "Remote, US",
                },
                persona_id=PID,
                skip_content_gates=True,
            )
            assert r.ok, r.reason
        r = await process_listing(
            session,
            {
                "company": "Dco",
                "title": "Data Engineer",
                "url": "https://example.com/other/1",
                "location": "Remote, US",
            },
            persona_id=OTHER,
            skip_content_gates=True,
        )
        assert r.ok, r.reason
        await session.commit()

        # Each profile sees only the jobs in its own table.
        pid_list = await _list_jobs(session, profile=PID)
        assert pid_list["total"] == 2
        assert {j["persona_id"] for j in pid_list["jobs"]} == {PID}

        other_list = await _list_jobs(session, profile=OTHER)
        assert other_list["total"] == 1
        assert {j["persona_id"] for j in other_list["jobs"]} == {OTHER}

        # meta/stats rolls every persona table into one view.
        from app.api.jobs import job_stats

        stats = await job_stats(session)
        assert stats["total"] == 3
        assert stats["by_status"].get("new") == 3
        assert stats["by_profile"][PID]["new"] == 2
        assert stats["by_profile"][OTHER]["new"] == 1
        assert stats["max_seq"] >= 2

    sess._engine = None
    sess._session_factory = None
    get_settings.cache_clear()
