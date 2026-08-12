from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.jobs import RangeDelete, _purge_jobs, delete_jobs_range
from app.config import get_settings
from app.db import session as sess
from app.db.enums import JobStatus
from app.db.models import Job
from app.db.session import get_session_factory, init_db
from app.services.deep_scan import deep_scan_job
from app.services.pipeline import process_listing


@pytest.mark.asyncio
async def test_deep_scan_failure_sets_scan_failed(tmp_path, monkeypatch):
    db_file = tmp_path / "scan_fail.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()

    async def boom(_url):
        class R:
            ok = False
            reason = "http_403"
            text = ""

        return R()

    monkeypatch.setattr("app.services.deep_scan.fetch_jd_http", boom)
    monkeypatch.setattr("app.services.deep_scan.fetch_jd_browser", boom)

    factory = get_session_factory()
    async with factory() as session:
        result = await process_listing(
            session,
            {
                "company": "FailCo",
                "title": "Senior Engineer",
                "url": "https://example.com/jobs/1",
                "location": "Remote, US",
            },
            skip_content_gates=True,
        )
        assert result.ok
        await session.commit()
        job = result.job
        out = await deep_scan_job(session, job, force=True)
        await session.commit()
        assert out["ok"] is False
        assert out["status"] == JobStatus.SCAN_FAILED.value
        await session.refresh(job)
        assert job.status == JobStatus.SCAN_FAILED.value
        assert job.notes and job.notes.startswith("deep_scan_failed:")


@pytest.mark.asyncio
async def test_purge_jobs_and_delete_range(tmp_path, monkeypatch):
    db_file = tmp_path / "delete.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()

    factory = get_session_factory()
    async with factory() as session:
        for i in range(1, 4):
            r = await process_listing(
                session,
                {
                    "company": f"Co{i}",
                    "title": "Senior Engineer",
                    "url": f"https://example.com/jobs/{i}",
                    "location": "Remote, US",
                },
                skip_content_gates=True,
            )
            assert r.ok
        await session.commit()
        rows = list((await session.execute(select(Job).order_by(Job.seq_no))).scalars().all())
        assert [j.seq_no for j in rows] == [1, 2, 3]

        deleted = await _purge_jobs(session, [rows[0]], remove_folders=True)
        await session.commit()
        assert len(deleted[0]) == 1

        remaining = list((await session.execute(select(Job).order_by(Job.seq_no))).scalars().all())
        assert [j.seq_no for j in remaining] == [2, 3]

        out = await delete_jobs_range(
            RangeDelete(seq_from=2, seq_to=3),
            db=session,
        )
        assert out["deleted"] == 2
        left = (await session.execute(select(Job))).scalars().all()
        assert left == []
