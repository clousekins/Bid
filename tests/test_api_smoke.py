from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session as sess
from app.db.enums import JobStatus
from app.db.models import Application, Job
from app.db.session import get_session_factory, init_db
from app.services.pipeline import process_listing
from app.validators.dedupe import normalize_company


@pytest.mark.asyncio
async def test_pipeline_seq_and_applied_skip(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
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

        # Mark first company applied via Application row
        first = rows[0]
        first.status = JobStatus.APPLIED.value
        session.add(
            Application(
                persona_id=first.persona_id,
                company=first.company,
                normalized_company=normalize_company(first.company),
                job_id=first.id,
                status="applied",
            )
        )
        await session.commit()

        r2 = await process_listing(
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
        assert r2.skipped
        assert r2.reason == "company_already_applied"

    sess._engine = None
    sess._session_factory = None
    get_settings.cache_clear()
