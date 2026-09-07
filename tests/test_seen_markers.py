"""Deleting a job must not leave it locked out of every future fetch.

A job's `job_source_seen` marker is written beside the job row, and nothing
ever removed it. Delete the job and the marker outlives it, so the next fetch
reads the listing, sees the marker, and skips it as `already_seen` — for good.
341 Jobright listings were stranded that way in one table.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.jobs import _purge_jobs
from app.config import get_settings
from app.db import session as sess
from app.db.models import Application, Job, JobSourceSeen, RejectedJob
from app.db.session import get_session_factory, init_db
from app.services.pipeline import (
    forget_job_markers,
    process_listing,
    prune_orphan_seen_markers,
)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path/'m.db').as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()
    async with get_session_factory()() as session:
        yield session


def _listing(**over):
    row = {
        "company": "Acme Analytics",
        "title": "Senior Data Engineer",
        "url": "https://jobright.ai/jobs/info/6a590efe686b4755d1e0e2be",
        "location": "United States (Remote)",
        "description": "Own the warehouse. " * 40,
        "source": "Jobright",
        "external_id": "6a590efe686b4755d1e0e2be",
    }
    row.update(over)
    return row


async def _insert(db, **over) -> Job:
    result = await process_listing(db, _listing(**over), persona_id=None)
    await db.commit()
    assert result.job is not None, result.reason
    return result.job


class TestDeleteLeavesNothingBehind:
    async def test_the_seen_marker_goes_with_the_job(self, db):
        job = await _insert(db)
        assert (await db.execute(select(JobSourceSeen))).scalars().all()
        await _purge_jobs(db, [job], remove_folders=False)
        await db.commit()
        assert (await db.execute(select(JobSourceSeen))).scalars().all() == []

    async def test_a_deleted_job_can_be_fetched_back(self, db):
        """The whole point: delete it, fetch again, it lands in the table."""
        job = await _insert(db)
        await _purge_jobs(db, [job], remove_folders=False)
        await db.commit()

        again = await process_listing(db, _listing(), persona_id=None)
        await db.commit()
        assert again.reason != "already_seen"
        assert again.job is not None and again.ok

    async def test_the_rejected_row_goes_too(self, db):
        job = await _insert(db)
        db.add(RejectedJob(company="Acme Analytics", title="Senior Data Engineer",
                           url=job.url, reason="not_remote", stage="stage1"))
        await db.commit()
        await _purge_jobs(db, [job], remove_folders=False)
        await db.commit()
        assert (await db.execute(select(RejectedJob))).scalars().all() == []

    async def test_applied_history_survives(self, db):
        """Applications are per company, not per job.

        Dropping one would let a later fetch offer an employer already applied
        to, so delete detaches it rather than erasing it.
        """
        job = await _insert(db)
        db.add(Application(persona_id="p1", company="Acme Analytics",
                           normalized_company="acme analytics", job_id=job.id,
                           status="applied"))
        await db.commit()
        await _purge_jobs(db, [job], remove_folders=False)
        await db.commit()
        rows = (await db.execute(select(Application))).scalars().all()
        assert len(rows) == 1 and rows[0].job_id is None

    async def test_the_source_is_matched_case_insensitively(self, db):
        """It has been written both "Jobright" and "jobright"."""
        job = await _insert(db)
        marker = (await db.execute(select(JobSourceSeen))).scalar_one()
        marker.source = "jobright"
        await db.commit()
        out = await forget_job_markers(db, [job])
        await db.commit()
        assert out["seen_markers"] == 1
        assert (await db.execute(select(JobSourceSeen))).scalars().all() == []


class TestPruningTheOnesAlreadyStranded:
    async def test_a_marker_with_no_job_is_dropped(self, db):
        db.add(JobSourceSeen(source="Jobright", external_id="deadbeef" * 3))
        await db.commit()
        assert await prune_orphan_seen_markers(db) == 1
        await db.commit()
        assert (await db.execute(select(JobSourceSeen))).scalars().all() == []

    async def test_a_marker_with_a_live_job_is_kept(self, db):
        await _insert(db)
        assert await prune_orphan_seen_markers(db) == 0
        await db.commit()
        assert len((await db.execute(select(JobSourceSeen))).scalars().all()) == 1

    async def test_it_reports_what_it_removed(self, db):
        await _insert(db)
        for i in range(3):
            db.add(JobSourceSeen(source="Jobright", external_id=f"orphan{i}"))
        await db.commit()
        assert await prune_orphan_seen_markers(db) == 3
