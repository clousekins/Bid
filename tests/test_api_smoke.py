from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session as sess
from app.db.models import Application, Job, utcnow
from app.db.session import get_session_factory, init_db
from app.services.pipeline import APPLY_GROUP, process_listing
from app.validators.dedupe import normalize_company


@pytest.mark.asyncio
async def test_pipeline_seq_and_applied_skip(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    # The company-already-applied skip fires once the whole crew has applied,
    # and is always on now, independent of ALLOW_COMPANY_MULTI_APPLY.
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()

    factory = get_session_factory()
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
            result = await process_listing(session, body, skip_content_gates=True)
            assert result.ok, result.reason
        await session.commit()
        rows = (await session.execute(select(Job).order_by(Job.seq_no))).scalars().all()
        assert [j.seq_no for j in rows] == [1, 2, 3]
        assert all(j.status == "new" for j in rows)

        # Past the one-per-company window, one crew member applied is not enough
        # to close the company: the others can still use it, so a second Acme job
        # is still let in. (Within the window the company rule blocks it outright.)
        acme = rows[0]
        acme.created_at = utcnow() - timedelta(days=11)
        normalized_acme = normalize_company(acme.company)
        session.add(
            Application(
                persona_id=APPLY_GROUP[0],
                company=acme.company,
                normalized_company=normalized_acme,
                job_id=acme.id,
                status="applied",
            )
        )
        await session.commit()

        r_partial = await process_listing(
            session,
            {
                "company": "Acme LLC",
                "title": "Staff Engineer",
                "url": "https://example.com/b",
                "location": "Remote, US",
            },
            skip_content_gates=True,
        )
        await session.commit()
        assert r_partial.ok, r_partial.reason

        # Once the whole crew has applied, the company is closed to ingest.
        for persona_id in APPLY_GROUP[1:]:
            session.add(
                Application(
                    persona_id=persona_id,
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
            skip_content_gates=True,
        )
        await session.commit()
        assert r2.skipped
        assert r2.reason == "company_already_applied"

    sess._engine = None
    sess._session_factory = None
    get_settings.cache_clear()
